#!/usr/bin/env python3
# ================================================================================
# DATEI: nvidia_video_analyzer.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

# -*- coding: utf-8 -*-
"""
================================================================================
NVIDIA Multi-Frame Fail Analyzer
================================================================================
WHY: NVIDIA Llama-3.2-90B Vision kann bis zu 16 Bilder gleichzeitig analysieren.
     Statt einzelner Screenshots senden wir eine Frame-Sequenz — das simuliert
     echte Video-Analyse und zeigt temporale Kausalität (was führte zum Fail?).
CONSEQUENCES: Root-Cause-Analyse in <30s statt manuellem Screenshot-Debugging.
================================================================================
"""

import asyncio
import base64
import json
import os
import urllib.error
import urllib.request
from typing import Optional


# ============================================================================
# FAIL-ANALYSIS PROMPT TEMPLATE
# ============================================================================
# WHY: Strukturierter Prompt erzwingt strukturiertes JSON-Output.
# CONSEQUENCES: NVIDIA Llama gibt exakt das Format zurück das wir parsen können.
FAIL_ANALYSIS_PROMPT = """Du analysierst eine Sequenz von {frame_count} Browser-Screenshots eines autonomen Survey-Workers.
Der Worker hat VERSAGT. Die Frames sind in zeitlicher Reihenfolge (Frame 1 = ältester, Frame {frame_count} = neuester/Fail-Punkt).

FEHLER-KONTEXT: {fail_context}

STEP-ANNOTATIONS (falls vorhanden):
{step_annotations}

Analysiere die komplette Frame-Sequenz und identifiziere die ROOT CAUSE.
Achte besonders auf:
- Welcher Frame zeigt den Moment wo es schief ging?
- Gab es ein Captcha das nicht gelöst wurde?
- Wurde auf ein falsches/unsichtbares Element geklickt?
- Gab es einen Timing-Fehler (zu schnell geklickt bevor Seite geladen)?
- Hat sich die Seite zwischen Frames NICHT verändert (Stillstand/Loop)?

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt:
{{
  "root_cause": "1-2 Sätze: Was ist schief gelaufen?",
  "affected_step": "Welcher Schritt hat versagt? (z.B. 'Klick auf Weiter-Button Seite 3')",
  "fix_recommendation": "Wie sollte der Worker das nächste Mal anders handeln?",
  "confidence_score": 0.85,
  "frame_evidence": "Frame 8 zeigt: [Beschreibung]",
  "captcha_detected": false,
  "timing_issue": false,
  "selector_issue": false,
  "loop_detected": false,
  "page_state_at_fail": "survey_active"
}}"""


async def analyze_fail_multiframe(
    keyframe_bytes: list[bytes],
    fail_context: str,
    nvidia_api_key: str,
    step_annotations: Optional[list[str]] = None,
    model: str = "meta/llama-3.2-90b-vision-instruct",
    nim_base_url: str = "https://integrate.api.nvidia.com/v1",
    timeout: int = 120,
    max_image_bytes: int = 150_000,
) -> dict:
    """
    Sendet bis zu 12 PNG-Keyframes als Multi-Image-Batch an NVIDIA NIM.
    WHY: NVIDIA Llama-90B unterstützt bis zu 16 Bilder pro Request.
         Das simuliert temporale Video-Analyse für Root-Cause-Detection.
    CONSEQUENCES: Gibt strukturiertes JSON mit root_cause, fix_recommendation etc. zurück.
                  Bei Fehler: error-Feld gesetzt, root_cause = "N/A" oder "API Error".
    """
    if not nvidia_api_key:
        return {"error": "NVIDIA_API_KEY nicht gesetzt", "root_cause": "N/A"}

    if not keyframe_bytes:
        return {"error": "Keine Keyframes vorhanden", "root_cause": "N/A"}

    # Maximal 12 Frames (NVIDIA Limit-Consideration + Token-Budget)
    frames = keyframe_bytes[:12]

    # Step-Annotations aufbereiten
    annotations_text = "Keine Step-Annotations verfügbar."
    if step_annotations:
        annotations_text = "\n".join(
            f"- Frame {i + 1}: {ann}" for i, ann in enumerate(step_annotations[:12])
        )

    # Content-Array aufbauen: Text + alle Frames als image_url
    content: list[dict] = [
        {
            "type": "text",
            "text": FAIL_ANALYSIS_PROMPT.format(
                frame_count=len(frames),
                fail_context=fail_context[:500],
                step_annotations=annotations_text,
            ),
        }
    ]

    for frame_data in frames:
        # Downscale wenn nötig (Pillow optional)
        img_bytes = _maybe_downscale(frame_data, max_image_bytes)
        b64 = base64.b64encode(img_bytes).decode("ascii")
        # Mime-Type: JPEG wenn Pillow downscaled hat, sonst PNG
        mime = "image/jpeg" if img_bytes[:2] == b"\xff\xd8" else "image/png"
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Du bist ein präziser Browser-Automation-Debugger. "
                    "Antworte AUSSCHLIESSLICH mit einem einzigen gültigen JSON-Objekt. "
                    "KEINE Prosa, KEINE Markdown-Blöcke."
                ),
            },
            {"role": "user", "content": content},
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
    }

    # HTTP Request in Thread (blockiert asyncio nicht)
    def _do_request() -> tuple[int, str, str]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{nim_base_url.rstrip('/')}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {nvidia_api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace"), ""
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            return e.code, "", err_body
        except urllib.error.URLError as e:
            return 0, "", f"URLError: {e.reason}"
        except Exception as e:
            return 0, "", f"Exception: {e}"

    try:
        status, body_text, err_text = await asyncio.wait_for(
            asyncio.to_thread(_do_request), timeout=timeout + 10
        )
    except asyncio.TimeoutError:
        return {"error": f"NVIDIA Timeout nach {timeout}s", "root_cause": "Timeout"}

    if status != 200:
        return {
            "error": f"NVIDIA HTTP {status}: {(err_text or body_text)[:300]}",
            "root_cause": "API Error",
        }

    # Response parsen
    try:
        data = json.loads(body_text)
        choices = data.get("choices", [])
        if not choices:
            return {"error": "Keine choices im NVIDIA Response", "root_cause": "Empty"}

        raw_content = choices[0].get("message", {}).get("content", "")
        if isinstance(raw_content, list):
            raw_content = "".join(c.get("text", "") for c in raw_content if isinstance(c, dict))

        # JSON aus Content parsen (ggf. Regex-Fallback für Prosa-Wrapper)
        try:
            result = json.loads(raw_content)
        except json.JSONDecodeError:
            import re

            match = re.search(r"\{[^{}]*\}", raw_content, re.DOTALL)
            if match:
                result = json.loads(match.group())
            else:
                return {
                    "error": f"JSON-Parse fehlgeschlagen: {raw_content[:200]}",
                    "root_cause": "Parse Error",
                }

        # Model-Info anhängen
        result["_model_used"] = data.get("model", model)
        result["_usage"] = data.get("usage", {})
        return result

    except Exception as e:
        return {"error": f"Parse error: {e}", "root_cause": "Unknown"}


def _maybe_downscale(png_bytes: bytes, max_bytes: int) -> bytes:
    """
    Verkleinert ein PNG wenn es zu groß ist (NVIDIA NIM Inline-Limit).
    WHY: NVIDIA NIM akzeptiert inline-base64 nur bis ~180KB.
    CONSEQUENCES: Nutzt Pillow wenn verfügbar, sonst Rohbytes.
    """
    if len(png_bytes) <= max_bytes:
        return png_bytes
    try:
        from PIL import Image
        from io import BytesIO

        img = Image.open(BytesIO(png_bytes))
        # Iterativ runterskalieren
        for scale in (0.7, 0.5, 0.35, 0.25):
            w, h = img.size
            new = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = BytesIO()
            new.save(buf, format="PNG", optimize=True)
            out = buf.getvalue()
            if len(out) <= max_bytes:
                return out
        # JPEG als letzter Ausweg
        buf = BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=55, optimize=True)
        return buf.getvalue()
    except ImportError:
        return png_bytes
    except Exception:
        return png_bytes
