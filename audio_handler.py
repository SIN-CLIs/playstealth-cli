#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Audio Handler — NVIDIA NIM Parakeet / Canary ASR für Survey-Audio-Fragen
================================================================================
WHY: Manche Umfragen spielen Audio-Clips ab (Werbe-Spots, Voice-Prompts, Musik,
     Sprach-Slogans) und fragen danach was gehört wurde. Die reine Vision-Pipeline
     kann das nicht — ein Bild von einem Audio-Player hilft niemandem.
CONSEQUENCES: Audio wird aus dem DOM extrahiert, an NVIDIA NIM geschickt, und der
     Transcript wird in den Vision-Prompt injiziert. Llama-3.2 Vision sieht dann den Text
     zusätzlich zum Screenshot und kann die Frage präzise beantworten.

INPUTS:
  - Audio-URL (MP3/WAV/OGG/OPUS/M4A) aus <audio src=...> oder <source src=...>
  - Optional: bereits heruntergeladene Bytes (data:-URL, blob)

OUTPUTS:
  - dict mit "transcript", "language", "confidence", "duration_sec", "model_used"
  - Bei Fehler: "error" Feld gesetzt, "transcript" = "" (nie None, damit Prompt-
    Injection defensiv bleibt)

SUPPORTED MODELS (NVIDIA NIM Inference):
  - Default:  nvidia/parakeet-tdt-0.6b-v2   (sehr schnell, Englisch-Fokus)
  - Fallback: nvidia/canary-1b-flash        (mehrsprachig, auch Deutsch)
  - Override: AUDIO_ASR_MODEL env
================================================================================
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


# ============================================================================
# PUBLIC API
# ============================================================================


@dataclass(frozen=True)
class AudioTranscript:
    """Strukturiertes Ergebnis einer ASR-Anfrage."""

    transcript: str
    language: str
    confidence: float
    duration_sec: float
    model_used: str
    source_url: str
    error: str = ""

    def to_prompt_block(self) -> str:
        """Formatiert den Transcript als Block für Injektion in den Vision-Prompt."""
        if self.error:
            return (
                "AUDIO-FRAGE ERKANNT (Transkription fehlgeschlagen):\n"
                f"- Fehler: {self.error}\n"
                "- Handle trotzdem: klicke Play, lies Visuelle Hinweise (Untertitel/Captions)."
            )
        if not self.transcript.strip():
            return "AUDIO-FRAGE ERKANNT (leerer Transcript — evtl. nur Musik/Ton)."
        preview = self.transcript.strip()
        if len(preview) > 800:
            preview = preview[:800] + "…"
        return (
            "AUDIO-FRAGE ERKANNT — GESPROCHENER TEXT IM CLIP (transkribiert):\n"
            f"Sprache: {self.language} | Dauer: {self.duration_sec:.1f}s | "
            f"Konfidenz: {self.confidence:.2f} | Modell: {self.model_used}\n"
            f'Transkript: "{preview}"\n'
            "Nutze diesen Transcript um die Folgefrage korrekt zu beantworten."
        )


async def transcribe_audio(
    audio_url: str,
    *,
    nvidia_api_key: str,
    model: str | None = None,
    fallback_models: tuple[str, ...] = (),
    nim_base_url: str = "https://integrate.api.nvidia.com/v1",
    timeout: int = 90,
    language_hint: str = "de",
    max_audio_bytes: int = 20_000_000,
) -> AudioTranscript:
    """
    Lädt Audio von `audio_url`, schickt es an NVIDIA NIM und gibt Transcript zurück.
    WHY: Survey-Audio-Fragen müssen in Text konvertiert werden, damit die Vision-
         Pipeline (Llama-3.2 Vision via NVIDIA NIM) die Frage semantisch verstehen kann.
    CONSEQUENCES:
       - Bei Download-Fehler: AudioTranscript mit error gesetzt (kein Crash)
       - Bei Primary-Model-Fehler: Fallback-Modelle werden durchprobiert
       - Bei vollständigem Fehler: transcript="" + error beschreibt Ursache
    """
    primary = model or os.environ.get("AUDIO_ASR_MODEL") or "nvidia/parakeet-tdt-0.6b-v2"
    fallbacks = fallback_models or (
        "nvidia/canary-1b-flash",
        "nvidia/parakeet-ctc-1.1b",
    )

    if not nvidia_api_key:
        return AudioTranscript(
            transcript="",
            language=language_hint,
            confidence=0.0,
            duration_sec=0.0,
            model_used=primary,
            source_url=audio_url,
            error="NVIDIA_API_KEY nicht gesetzt",
        )

    # 1) Audio herunterladen (akzeptiert http(s), data:, file:)
    try:
        audio_bytes, mime = await asyncio.to_thread(_download_audio, audio_url, max_audio_bytes)
    except Exception as e:
        return AudioTranscript(
            transcript="",
            language=language_hint,
            confidence=0.0,
            duration_sec=0.0,
            model_used=primary,
            source_url=audio_url,
            error=f"Download fehlgeschlagen: {e}",
        )

    duration_sec = _estimate_duration_seconds(audio_bytes, mime)

    # 2) Modell-Kaskade: primary → fallback 1 → fallback 2 …
    last_error = ""
    candidates = [primary, *(m for m in fallbacks if m and m != primary)]
    for candidate_model in candidates:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    _request_nim_asr,
                    audio_bytes,
                    mime,
                    candidate_model,
                    nvidia_api_key,
                    nim_base_url,
                    language_hint,
                    timeout,
                ),
                timeout=timeout + 15,
            )
        except asyncio.TimeoutError:
            last_error = f"Timeout bei Modell {candidate_model}"
            continue
        except Exception as e:
            last_error = f"{candidate_model}: {e}"
            continue

        if result.get("ok"):
            return AudioTranscript(
                transcript=str(result.get("transcript", "")).strip(),
                language=str(result.get("language") or language_hint),
                confidence=float(result.get("confidence") or 0.0),
                duration_sec=float(result.get("duration_sec") or duration_sec),
                model_used=candidate_model,
                source_url=audio_url,
            )
        last_error = str(result.get("error", "unknown"))

    return AudioTranscript(
        transcript="",
        language=language_hint,
        confidence=0.0,
        duration_sec=duration_sec,
        model_used=primary,
        source_url=audio_url,
        error=last_error or "Alle ASR-Modelle fehlgeschlagen",
    )


# ============================================================================
# INTERNALS
# ============================================================================


def _download_audio(url: str, max_bytes: int) -> tuple[bytes, str]:
    """
    Lädt Audio von http(s), data: oder file: URL. Begrenzt auf max_bytes.
    WHY: Audio-Quellen in Umfragen sind meist <audio src="https://..."> oder
         data:audio/ogg;base64,... — beides muss unterstützt werden.
    """
    if url.startswith("data:"):
        header, _, payload = url.partition(",")
        mime = header.split(";")[0].removeprefix("data:") or "audio/mpeg"
        if "base64" in header:
            raw = base64.b64decode(payload + "=" * ((4 - len(payload) % 4) % 4))
        else:
            raw = payload.encode("utf-8")
        if len(raw) > max_bytes:
            raise ValueError(f"Audio zu groß: {len(raw)} bytes (max {max_bytes})")
        return raw, mime

    if url.startswith("file://") or url.startswith("/"):
        p = Path(url.removeprefix("file://"))
        raw = p.read_bytes()
        if len(raw) > max_bytes:
            raise ValueError(f"Audio zu groß: {len(raw)} bytes (max {max_bytes})")
        return raw, _guess_mime_from_suffix(p.suffix)

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "HeyPiggyWorker/3.2 (+https://github.com/OpenSIN-AI/A2A-SIN-Worker-heypiggy)",
            "Accept": "audio/*, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        content_length = resp.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise ValueError(f"Audio zu groß laut Content-Length: {content_length}")
        raw = resp.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError(f"Audio zu groß: >{max_bytes} bytes")
        mime = resp.headers.get("Content-Type", "").split(";")[0].strip() or "audio/mpeg"
        return raw, mime


def _guess_mime_from_suffix(suffix: str) -> str:
    mapping = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".oga": "audio/ogg",
        ".opus": "audio/opus",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".flac": "audio/flac",
        ".webm": "audio/webm",
    }
    return mapping.get(suffix.lower(), "audio/mpeg")


def _estimate_duration_seconds(audio_bytes: bytes, mime: str) -> float:
    """
    Grobe Duration-Schätzung ohne ffprobe (nur als Hinweis für Logs/Prompt).
    WHY: Manche NIMs geben keine Duration zurück; der Prompt profitiert aber
         schon von ~gerundeten Sekunden damit das Vision-LLM den Clip-Kontext versteht.
    CONSEQUENCES: Schätzung ist ungenau (nur Byte-Rate), aber besser als 0.
    """
    size = len(audio_bytes)
    approx_bitrate = {
        "audio/mpeg": 128_000,
        "audio/mp4": 128_000,
        "audio/ogg": 96_000,
        "audio/opus": 48_000,
        "audio/wav": 1_411_200,
        "audio/flac": 800_000,
        "audio/webm": 96_000,
    }.get(mime.lower(), 128_000)
    return round((size * 8) / max(approx_bitrate, 1), 2)


def _request_nim_asr(
    audio_bytes: bytes,
    mime: str,
    model: str,
    api_key: str,
    base_url: str,
    language_hint: str,
    timeout: int,
) -> dict:
    """
    Ruft NVIDIA NIM ASR im OpenAI-kompatiblen `/audio/transcriptions`-Format auf.
    WHY: NVIDIA NIM stellt für Parakeet/Canary OpenAI-ähnliche Endpunkte bereit.
    CONSEQUENCES: multipart/form-data Upload, damit wir die Rohbytes ohne Base64
         senden (spart Bandbreite). Wenn der Endpoint nicht verfügbar ist,
         fällt `transcribe_audio` auf das nächste Modell zurück.
    """
    boundary = f"----HeyPiggyASR{int(time.time() * 1000)}"
    suffix = _mime_to_suffix(mime)
    filename = f"clip{suffix}"

    parts: list[bytes] = []
    for field_name, field_value in (
        ("model", model),
        ("language", language_hint),
        ("response_format", "verbose_json"),
        ("temperature", "0"),
    ):
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n{field_value}\r\n'.encode()
        )
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    )
    parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
    parts.append(audio_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())

    body = b"".join(parts)
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/audio/transcriptions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            return {
                "ok": True,
                "transcript": data.get("text", ""),
                "language": data.get("language", language_hint),
                "confidence": _average_confidence(data.get("segments", [])),
                "duration_sec": float(data.get("duration", 0.0) or 0.0),
            }
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        return {"ok": False, "error": f"HTTP {e.code}: {err_body[:240]}"}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"URLError: {e.reason}"}
    except Exception as e:
        return {"ok": False, "error": f"Exception: {e}"}


def _mime_to_suffix(mime: str) -> str:
    mapping = {
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/flac": ".flac",
        "audio/webm": ".webm",
    }
    return mapping.get(mime.lower(), ".mp3")


def _average_confidence(segments: list[dict] | None) -> float:
    if not segments:
        return 0.0
    confs: list[float] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        val = seg.get("avg_logprob")
        if isinstance(val, (int, float)):
            # Log-Prob → Wahrscheinlichkeit (clamped)
            import math

            confs.append(max(0.0, min(1.0, math.exp(float(val)))))
    if not confs:
        return 0.0
    return round(sum(confs) / len(confs), 3)
