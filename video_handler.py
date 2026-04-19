#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Video Handler — NVIDIA NIM Video Understanding für Survey-Video-Fragen
================================================================================
WHY: Manche Umfragen zeigen Video-Clips (Werbespots, Erklärvideos, Produkt-Demos)
     und stellen danach Fragen zum Inhalt ("Welche Marke wurde gezeigt?",
     "Was macht die Person im Clip?"). Einzelne Screenshots reichen nicht — wir
     brauchen eine semantische Zusammenfassung über die Zeit.
CONSEQUENCES: Video wird geladen, an NVIDIA NIM Video Understanding geschickt
     (Cosmos-Reason / VITA / llama-3.2 Video-Variante), und eine strukturierte
     Beschreibung wird in den Vision-Prompt injiziert. Llama-3.2 Vision sieht dann:
       - Was ist im Video passiert? (Aktionen, Objekte, Personen, Marken)
       - Welche Stimmung/Genre? (Werbung, Doku, Comedy, …)
       - Gab es Text/Logos/Untertitel?

STRATEGIE:
  1) Wenn NVIDIA NIM ein direktes Video-Endpoint akzeptiert (video_url), nutzen.
  2) Fallback: ffmpeg extrahiert N Keyframes + Audio-Track → audio_handler
     → Frames werden als Multi-Image an Llama-3.2-90B-Vision geschickt.
  3) Wenn auch das fehlschlägt: Rückgabe mit error-Feld, Worker handelt weiter.

SUPPORTED MODELS (konfigurierbar):
  - Default:  nvidia/cosmos-reason1-7b     (Video-Reasoning, sehr gut)
  - Fallback: nvidia/vita-1.5              (multimodal inkl. Audio)
  - Frame-Fallback: meta/llama-3.2-90b-vision-instruct (über nvidia_video_analyzer)
================================================================================
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


# ============================================================================
# PUBLIC API
# ============================================================================


@dataclass(frozen=True)
class VideoUnderstanding:
    """Strukturiertes Ergebnis einer Video-Understanding-Anfrage."""

    summary: str
    objects: tuple[str, ...]
    actions: tuple[str, ...]
    brands: tuple[str, ...]
    on_screen_text: str
    spoken_transcript: str
    duration_sec: float
    frame_count: int
    model_used: str
    source_url: str
    error: str = ""
    raw_response: dict = field(default_factory=dict)

    def to_prompt_block(self) -> str:
        """Formatiert das Verständnis als Block für Injektion in den Vision-Prompt."""
        if self.error and not self.summary:
            return (
                "VIDEO-FRAGE ERKANNT (Verständnis fehlgeschlagen):\n"
                f"- Fehler: {self.error}\n"
                "- Handle trotzdem: klicke Play, warte auf Untertitel/Captions, "
                "nutze ersichtliche Frames."
            )
        lines = [
            "VIDEO-FRAGE ERKANNT — SEMANTISCHE ANALYSE DES CLIPS:",
            f"Modell: {self.model_used} | Dauer: {self.duration_sec:.1f}s | "
            f"Frames analysiert: {self.frame_count}",
            f"Zusammenfassung: {self.summary}" if self.summary else "",
        ]
        if self.actions:
            lines.append(f"Aktionen/Handlungen: {', '.join(self.actions[:8])}")
        if self.objects:
            lines.append(f"Sichtbare Objekte/Szenen: {', '.join(self.objects[:10])}")
        if self.brands:
            lines.append(f"Marken/Logos gesichtet: {', '.join(self.brands[:6])}")
        if self.on_screen_text:
            preview = self.on_screen_text.strip()
            if len(preview) > 300:
                preview = preview[:300] + "…"
            lines.append(f"Bildschirmtext/Caption: {preview}")
        if self.spoken_transcript:
            preview = self.spoken_transcript.strip()
            if len(preview) > 500:
                preview = preview[:500] + "…"
            lines.append(f'Gesprochener Text: "{preview}"')
        lines.append(
            "Nutze diese Analyse um Fragen zum Video-Inhalt präzise zu beantworten "
            "(Marke, Produkt, Aktion, Stimmung, etc.)."
        )
        return "\n".join(line for line in lines if line)


async def understand_video(
    video_url: str,
    *,
    nvidia_api_key: str,
    model: str | None = None,
    fallback_models: tuple[str, ...] = (),
    nim_base_url: str = "https://integrate.api.nvidia.com/v1",
    timeout: int = 180,
    frame_count: int = 8,
    max_video_bytes: int = 80_000_000,
    audio_transcriber=None,
) -> VideoUnderstanding:
    """
    Analysiert ein Video semantisch über NVIDIA NIM Video Understanding.
    WHY: Survey-Video-Fragen brauchen mehr als Screenshots — sie brauchen
         temporale + semantische Interpretation.
    CONSEQUENCES:
       - Versucht zuerst direktes Video-Endpoint (wenn Modell es unterstützt).
       - Fallback: Keyframe-Extraktion via ffmpeg → Multi-Image Vision.
       - audio_transcriber: optional callable(url)->AudioTranscript für Tonspur.
       - Bei total-Fehler: error-Feld gesetzt, summary="", kein Crash.
    """
    primary = model or os.environ.get("VIDEO_UNDERSTANDING_MODEL") or "nvidia/cosmos-reason1-7b"
    fallbacks = fallback_models or (
        "nvidia/vita-1.5",
        "meta/llama-3.2-90b-vision-instruct",
    )

    if not nvidia_api_key:
        return VideoUnderstanding(
            summary="",
            objects=(),
            actions=(),
            brands=(),
            on_screen_text="",
            spoken_transcript="",
            duration_sec=0.0,
            frame_count=0,
            model_used=primary,
            source_url=video_url,
            error="NVIDIA_API_KEY nicht gesetzt",
        )

    # 1) Versuch: direkter Video-Endpoint (falls NIM es unterstützt via URL)
    candidates = [primary, *(m for m in fallbacks if m and m != primary)]
    last_error = ""
    for candidate_model in candidates:
        try:
            direct = await asyncio.wait_for(
                asyncio.to_thread(
                    _request_nim_video_direct,
                    video_url,
                    candidate_model,
                    nvidia_api_key,
                    nim_base_url,
                    timeout,
                ),
                timeout=timeout + 15,
            )
        except asyncio.TimeoutError:
            last_error = f"Timeout direct bei {candidate_model}"
            direct = {"ok": False, "error": last_error}
        except Exception as e:
            last_error = f"{candidate_model} direct: {e}"
            direct = {"ok": False, "error": last_error}

        if direct.get("ok"):
            parsed = _parse_video_response(direct.get("data", {}))
            return VideoUnderstanding(
                summary=parsed["summary"],
                objects=tuple(parsed["objects"]),
                actions=tuple(parsed["actions"]),
                brands=tuple(parsed["brands"]),
                on_screen_text=parsed["on_screen_text"],
                spoken_transcript=parsed["spoken_transcript"],
                duration_sec=parsed["duration_sec"],
                frame_count=parsed["frame_count"] or 1,
                model_used=candidate_model,
                source_url=video_url,
                raw_response=direct.get("data", {}),
            )
        last_error = str(direct.get("error", "unknown"))

    # 2) Fallback: Frame-Extraktion + Multi-Image Vision + optional Audio
    try:
        video_bytes = await asyncio.to_thread(_download_video, video_url, max_video_bytes)
    except Exception as e:
        return VideoUnderstanding(
            summary="",
            objects=(),
            actions=(),
            brands=(),
            on_screen_text="",
            spoken_transcript="",
            duration_sec=0.0,
            frame_count=0,
            model_used=primary,
            source_url=video_url,
            error=f"Video-Download fehlgeschlagen: {e} (vorher: {last_error})",
        )

    frames, duration_sec = await asyncio.to_thread(_extract_keyframes, video_bytes, frame_count)
    if not frames:
        return VideoUnderstanding(
            summary="",
            objects=(),
            actions=(),
            brands=(),
            on_screen_text="",
            spoken_transcript="",
            duration_sec=duration_sec,
            frame_count=0,
            model_used=primary,
            source_url=video_url,
            error=f"Keyframe-Extraktion fehlgeschlagen (ffmpeg?). Vorher: {last_error}",
        )

    # Audio-Spur optional transkribieren
    spoken = ""
    if audio_transcriber is not None:
        try:
            audio_bytes = await asyncio.to_thread(_extract_audio_track, video_bytes)
            if audio_bytes:
                import tempfile as _tf

                with _tf.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
                    tmp.write(audio_bytes)
                    tmp_path = tmp.name
                try:
                    audio_result = await audio_transcriber(f"file://{tmp_path}")
                    spoken = getattr(audio_result, "transcript", "") or ""
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
        except Exception as e:
            spoken = ""
            last_error = f"Audio-Extraktion warnte: {e}"

    # Frame-basierte Analyse via bereits existierendem nvidia_video_analyzer
    try:
        from nvidia_video_analyzer import analyze_fail_multiframe

        fail_like_context = (
            "VIDEO-CLIP-VERSTÄNDNIS für Survey-Frage. Kein Fehler — beschreibe nur "
            "den Inhalt des Clips strukturiert: Aktionen, Objekte, sichtbare Marken, "
            "Bildschirmtexte, Stimmung. Antworte auf Deutsch."
        )
        analysis = await analyze_fail_multiframe(
            keyframe_bytes=frames,
            fail_context=fail_like_context,
            nvidia_api_key=nvidia_api_key,
            model="meta/llama-3.2-90b-vision-instruct",
            nim_base_url=nim_base_url,
            timeout=timeout,
        )
    except Exception as e:
        return VideoUnderstanding(
            summary="",
            objects=(),
            actions=(),
            brands=(),
            on_screen_text="",
            spoken_transcript=spoken,
            duration_sec=duration_sec,
            frame_count=len(frames),
            model_used="meta/llama-3.2-90b-vision-instruct",
            source_url=video_url,
            error=f"Frame-Vision fehlgeschlagen: {e}",
        )

    if analysis.get("error") and not analysis.get("root_cause"):
        return VideoUnderstanding(
            summary="",
            objects=(),
            actions=(),
            brands=(),
            on_screen_text="",
            spoken_transcript=spoken,
            duration_sec=duration_sec,
            frame_count=len(frames),
            model_used="meta/llama-3.2-90b-vision-instruct",
            source_url=video_url,
            error=str(analysis.get("error")),
        )

    # analyze_fail_multiframe-Response in VideoUnderstanding-Format mappen.
    # Das Modell gibt Prosa — wir extrahieren die wichtigsten Elemente.
    summary_text = str(analysis.get("frame_evidence") or analysis.get("root_cause", "")).strip()
    if not summary_text:
        summary_text = "Video-Clip analysiert — siehe Frame-Evidenz."

    return VideoUnderstanding(
        summary=summary_text,
        objects=(),
        actions=(),
        brands=(),
        on_screen_text="",
        spoken_transcript=spoken,
        duration_sec=duration_sec,
        frame_count=len(frames),
        model_used=str(analysis.get("_model_used", "meta/llama-3.2-90b-vision-instruct")),
        source_url=video_url,
        raw_response=analysis,
    )


# ============================================================================
# DIRECT NIM VIDEO REQUEST
# ============================================================================

_VIDEO_PROMPT = """Analysiere das folgende Video und antworte AUSSCHLIESSLICH mit einem JSON-Objekt:
{
  "summary": "2-3 Sätze was passiert",
  "objects": ["objekt1", "objekt2"],
  "actions": ["aktion1", "aktion2"],
  "brands": ["marke1"],
  "on_screen_text": "alle lesbaren Texte/Logos/Captions",
  "spoken_transcript": "was wird gesprochen (falls verständlich)",
  "duration_sec": 15.0
}
KEINE Prosa, KEIN Markdown, NUR dieses JSON."""


def _request_nim_video_direct(
    video_url: str,
    model: str,
    api_key: str,
    base_url: str,
    timeout: int,
) -> dict:
    """
    Sendet eine Video-URL direkt an den Chat-Completion-Endpoint.
    WHY: Cosmos-Reason / VITA können Videos direkt per URL verarbeiten, spart
         uns die Keyframe-Extraktion wenn ffmpeg fehlt.
    CONSEQUENCES: Wenn Endpoint nicht unterstützt → HTTP-Fehler → Fallback greift.
    """
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VIDEO_PROMPT},
                    {"type": "video_url", "video_url": {"url": video_url}},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
    }

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            return {"ok": True, "data": data}
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


def _parse_video_response(data: dict) -> dict:
    """Extrahiert die Felder aus einer NIM Chat-Completion Response."""
    default = {
        "summary": "",
        "objects": [],
        "actions": [],
        "brands": [],
        "on_screen_text": "",
        "spoken_transcript": "",
        "duration_sec": 0.0,
        "frame_count": 1,
    }
    try:
        choices = data.get("choices", [])
        if not choices:
            return default
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))

        parsed = _extract_json(content)
        if not parsed:
            return default

        return {
            "summary": str(parsed.get("summary", "")).strip(),
            "objects": [str(x) for x in parsed.get("objects", []) if str(x).strip()],
            "actions": [str(x) for x in parsed.get("actions", []) if str(x).strip()],
            "brands": [str(x) for x in parsed.get("brands", []) if str(x).strip()],
            "on_screen_text": str(parsed.get("on_screen_text", "")),
            "spoken_transcript": str(parsed.get("spoken_transcript", "")),
            "duration_sec": float(parsed.get("duration_sec", 0.0) or 0.0),
            "frame_count": int(parsed.get("frame_count", 1) or 1),
        }
    except Exception:
        return default


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        # Markdown-Block entfernen
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
        if "```" in text:
            text = text.split("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


# ============================================================================
# VIDEO DOWNLOAD & KEYFRAME EXTRACTION
# ============================================================================


def _download_video(url: str, max_bytes: int) -> bytes:
    """
    Lädt Video von http(s), data: oder file: URL.
    WHY: Survey-Videos sind oft <video src="https://cdn.../clip.mp4"> oder
         blob: URLs die aus Bridge-Kontext als data-URL kommen.
    """
    if url.startswith("data:"):
        header, _, payload = url.partition(",")
        if "base64" in header:
            raw = base64.b64decode(payload + "=" * ((4 - len(payload) % 4) % 4))
        else:
            raw = payload.encode("utf-8")
        if len(raw) > max_bytes:
            raise ValueError(f"Video zu groß: {len(raw)} bytes (max {max_bytes})")
        return raw

    if url.startswith("file://") or url.startswith("/"):
        p = Path(url.removeprefix("file://"))
        raw = p.read_bytes()
        if len(raw) > max_bytes:
            raise ValueError(f"Video zu groß: {len(raw)} bytes (max {max_bytes})")
        return raw

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "HeyPiggyWorker/3.2 (+https://github.com/OpenSIN-AI/A2A-SIN-Worker-heypiggy)",
            "Accept": "video/*, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError(f"Video zu groß: >{max_bytes} bytes")
        return raw


def _extract_keyframes(video_bytes: bytes, frame_count: int) -> tuple[list[bytes], float]:
    """
    Extrahiert gleichmäßig verteilte Keyframes aus Video-Bytes via ffmpeg.
    WHY: Frame-basierte Modelle (Llama-3.2-Vision) brauchen diskrete PNG-Frames.
         ffmpeg ist Standard auf HF Spaces und Linux-VMs.
    CONSEQUENCES: Ohne ffmpeg → leere Liste, Fallback-Strategie greift.
    """
    if shutil.which("ffmpeg") is None:
        return [], 0.0

    frame_count = max(2, min(frame_count, 12))
    with tempfile.TemporaryDirectory(prefix="heypiggy_video_") as tmpdir:
        tmp_path = Path(tmpdir)
        video_path = tmp_path / "input.mp4"
        video_path.write_bytes(video_bytes)

        # Duration ermitteln via ffprobe
        duration_sec = _probe_duration(video_path)
        if duration_sec <= 0:
            duration_sec = 10.0  # Sicherer Default

        interval = max(duration_sec / (frame_count + 1), 0.5)
        frame_paths: list[Path] = []
        for i in range(frame_count):
            ts = (i + 1) * interval
            out_path = tmp_path / f"frame_{i:02d}.png"
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-ss",
                        f"{ts:.2f}",
                        "-i",
                        str(video_path),
                        "-frames:v",
                        "1",
                        "-q:v",
                        "3",
                        "-y",
                        str(out_path),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
                if out_path.exists() and out_path.stat().st_size > 0:
                    frame_paths.append(out_path)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue

        frames = [p.read_bytes() for p in frame_paths]
        return frames, duration_sec


def _probe_duration(video_path: Path) -> float:
    if shutil.which("ffprobe") is None:
        return 0.0
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return float(result.stdout.strip() or 0.0)
    except (subprocess.TimeoutExpired, ValueError):
        return 0.0


def _extract_audio_track(video_bytes: bytes) -> bytes:
    """
    Extrahiert die Audio-Spur aus einem Video als OGG/Opus (klein + hochwertig).
    WHY: Für Survey-Video-Fragen ist der gesprochene Text oft die entscheidende
         Information; die Video-Vision-Modelle verpassen Audio-Details.
    CONSEQUENCES: Ohne ffmpeg → b"".
    """
    if shutil.which("ffmpeg") is None:
        return b""
    with tempfile.TemporaryDirectory(prefix="heypiggy_audio_") as tmpdir:
        tmp_path = Path(tmpdir)
        vpath = tmp_path / "input.mp4"
        apath = tmp_path / "track.ogg"
        vpath.write_bytes(video_bytes)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-i",
                    str(vpath),
                    "-vn",
                    "-acodec",
                    "libopus",
                    "-b:a",
                    "48k",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-y",
                    str(apath),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            if apath.exists():
                return apath.read_bytes()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return b""
    return b""
