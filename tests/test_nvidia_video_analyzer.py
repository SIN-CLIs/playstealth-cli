#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Tests für nvidia_video_analyzer.py — Multi-Frame NVIDIA NIM Fail-Analyse
================================================================================
WHY: Die NVIDIA-API-Integration hat viele Fehlerpfade (Timeout, HTTP-Fehler,
     JSON-Parse-Fehler, leere Responses). Jeder Pfad muss getestet sein.
CONSEQUENCES: Ohne Tests würden API-Fehler den Worker zum Absturz bringen
     statt saubere error-Dicts zurückzugeben.
================================================================================
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from nvidia_video_analyzer import (
    FAIL_ANALYSIS_PROMPT,
    _maybe_downscale,
    analyze_fail_multiframe,
)


# ============================================================================
# HILFSFUNKTIONEN
# ============================================================================


def _fake_png(size: int = 100) -> bytes:
    """Erzeugt einen Fake-PNG-Blob mit gegebener Größe."""
    # PNG-Header + Padding — kein echtes Bild, aber ausreichend für Tests.
    header = b"\x89PNG\r\n\x1a\n"
    return header + b"\x00" * max(0, size - len(header))


def _make_nvidia_response(analysis: dict) -> str:
    """Baut eine NVIDIA-NIM-kompatible JSON-Response."""
    return json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(analysis, ensure_ascii=False),
                    }
                }
            ],
            "model": "meta/llama-3.2-90b-vision-instruct",
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
        }
    )


# ============================================================================
# UNIT TESTS — analyze_fail_multiframe (Input-Validierung)
# ============================================================================


class TestAnalyzeFailMultiframeValidation:
    """Tests für Input-Validierung vor dem API-Call."""

    @pytest.mark.asyncio
    async def test_no_api_key(self):
        """WHY: Ohne API-Key darf kein Request gesendet werden."""
        result = await analyze_fail_multiframe(
            keyframe_bytes=[_fake_png()],
            fail_context="test fail",
            nvidia_api_key="",
        )
        assert "error" in result
        assert "NVIDIA_API_KEY" in result["error"]
        assert result["root_cause"] == "N/A"

    @pytest.mark.asyncio
    async def test_no_keyframes(self):
        """WHY: Ohne Keyframes gibt es nichts zu analysieren."""
        result = await analyze_fail_multiframe(
            keyframe_bytes=[],
            fail_context="test fail",
            nvidia_api_key="nvapi-test",
        )
        assert "error" in result
        assert "Keine Keyframes" in result["error"]

    @pytest.mark.asyncio
    async def test_max_12_frames(self):
        """WHY: Mehr als 12 Frames werden abgeschnitten (NVIDIA Token-Budget)."""
        # 15 Frames senden, aber nur 12 sollten im Payload ankommen
        frames = [_fake_png(50) for _ in range(15)]

        # Mock den HTTP-Request um den Payload zu inspizieren
        captured_payload = {}

        def _mock_request():
            return (200, _make_nvidia_response({"root_cause": "test"}), "")

        with patch("nvidia_video_analyzer.urllib.request") as mock_urllib:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = _make_nvidia_response(
                {"root_cause": "test"}
            ).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urllib.urlopen.return_value = mock_resp

            # Capture den Request um die Anzahl der Bilder zu prüfen
            original_request = mock_urllib.Request

            def capture_request(url, data=None, **kwargs):
                if data:
                    captured_payload["data"] = json.loads(data.decode("utf-8"))
                req = MagicMock()
                return req

            mock_urllib.Request = capture_request

            result = await analyze_fail_multiframe(
                keyframe_bytes=frames,
                fail_context="test",
                nvidia_api_key="nvapi-test",
            )

        # Prüfe dass der Payload maximal 12 image_url Einträge hat
        if "data" in captured_payload:
            content = captured_payload["data"]["messages"][1]["content"]
            image_entries = [c for c in content if c.get("type") == "image_url"]
            assert len(image_entries) <= 12


# ============================================================================
# UNIT TESTS — analyze_fail_multiframe (API-Responses)
# ============================================================================


class TestAnalyzeFailMultiframeResponses:
    """Tests für verschiedene API-Response-Szenarien."""

    @pytest.mark.asyncio
    async def test_successful_analysis(self):
        """WHY: Erfolgreiche Analyse muss alle Felder korrekt parsen."""
        expected = {
            "root_cause": "Weiter-Button war nicht sichtbar (unter dem Fold)",
            "affected_step": "Klick auf Weiter-Button Seite 3",
            "fix_recommendation": "Scroll-Down vor Klick-Versuch",
            "confidence_score": 0.92,
            "frame_evidence": "Frame 8 zeigt: Button ist 200px unter dem Viewport",
            "captcha_detected": False,
            "timing_issue": False,
            "selector_issue": True,
            "loop_detected": False,
            "page_state_at_fail": "survey_active",
        }

        with patch("nvidia_video_analyzer.urllib.request") as mock_urllib:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = _make_nvidia_response(expected).encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urllib.urlopen.return_value = mock_resp
            mock_urllib.Request = MagicMock(return_value=MagicMock())

            result = await analyze_fail_multiframe(
                keyframe_bytes=[_fake_png()],
                fail_context="Survey-Seite 3 reagiert nicht auf Klick",
                nvidia_api_key="nvapi-test123",
                step_annotations=["Frame 1: Dashboard", "Frame 2: Survey Start"],
            )

        assert result["root_cause"] == expected["root_cause"]
        assert result["confidence_score"] == 0.92
        assert result["selector_issue"] is True
        assert "_model_used" in result
        assert "_usage" in result

    @pytest.mark.asyncio
    async def test_http_error_returns_error_dict(self):
        """WHY: HTTP-Fehler müssen als sauberes error-Dict zurückkommen, kein Crash."""
        with patch("nvidia_video_analyzer.urllib.request") as mock_urllib:
            import urllib.error

            mock_urllib.Request = MagicMock(return_value=MagicMock())
            mock_error = urllib.error.HTTPError(
                url="test", code=429, msg="Rate Limit", hdrs={}, fp=None
            )
            mock_error.read = MagicMock(return_value=b"rate limited")
            mock_urllib.urlopen.side_effect = mock_error

            result = await analyze_fail_multiframe(
                keyframe_bytes=[_fake_png()],
                fail_context="test",
                nvidia_api_key="nvapi-test",
            )

        assert "error" in result
        assert "429" in result["error"]
        assert result["root_cause"] == "API Error"

    @pytest.mark.asyncio
    async def test_timeout_returns_error_dict(self):
        """WHY: Timeout darf den Worker nicht crashen."""
        with patch("nvidia_video_analyzer.asyncio.to_thread") as mock_thread:
            mock_thread.side_effect = asyncio.TimeoutError()

            result = await analyze_fail_multiframe(
                keyframe_bytes=[_fake_png()],
                fail_context="test",
                nvidia_api_key="nvapi-test",
                timeout=5,
            )

        assert "error" in result
        assert "Timeout" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_choices_returns_error(self):
        """WHY: Leere choices in der Response müssen erkannt werden."""
        empty_response = json.dumps({"choices": [], "model": "test"})

        with patch("nvidia_video_analyzer.urllib.request") as mock_urllib:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = empty_response.encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urllib.urlopen.return_value = mock_resp
            mock_urllib.Request = MagicMock(return_value=MagicMock())

            result = await analyze_fail_multiframe(
                keyframe_bytes=[_fake_png()],
                fail_context="test",
                nvidia_api_key="nvapi-test",
            )

        assert "error" in result
        assert "choices" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_invalid_json_in_content_uses_regex_fallback(self):
        """WHY: Wenn NVIDIA Prosa um das JSON wickelt, muss Regex-Fallback greifen."""
        # Content mit Prosa-Wrapper um das JSON
        wrapped = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": 'Hier ist meine Analyse: {"root_cause": "Test", "confidence_score": 0.5} Das war es.',
                        }
                    }
                ],
                "model": "test",
            }
        )

        with patch("nvidia_video_analyzer.urllib.request") as mock_urllib:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = wrapped.encode()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urllib.urlopen.return_value = mock_resp
            mock_urllib.Request = MagicMock(return_value=MagicMock())

            result = await analyze_fail_multiframe(
                keyframe_bytes=[_fake_png()],
                fail_context="test",
                nvidia_api_key="nvapi-test",
            )

        assert result["root_cause"] == "Test"
        assert result["confidence_score"] == 0.5


# ============================================================================
# UNIT TESTS — _maybe_downscale
# ============================================================================


class TestMaybeDownscale:
    """Tests für die Bild-Downscale-Logik."""

    def test_small_image_unchanged(self):
        """WHY: Bilder unter dem Limit dürfen nicht verändert werden."""
        small = _fake_png(100)
        result = _maybe_downscale(small, max_bytes=200)
        assert result is small  # Exakt dasselbe Objekt (kein Copy)

    def test_large_image_without_pillow(self):
        """WHY: Ohne Pillow muss das Rohbild zurückgegeben werden (Fallback)."""
        large = _fake_png(500)
        with patch.dict("sys.modules", {"PIL": None, "PIL.Image": None}):
            result = _maybe_downscale(large, max_bytes=100)
        # Ohne Pillow: Rohbytes zurück (kann nicht verkleinern)
        assert len(result) >= 100  # Nicht verkleinert, aber kein Crash


# ============================================================================
# PROMPT TEMPLATE TEST
# ============================================================================


class TestPromptTemplate:
    """Tests für das FAIL_ANALYSIS_PROMPT Template."""

    def test_prompt_has_placeholders(self):
        """WHY: Template muss die richtigen Platzhalter haben."""
        assert "{frame_count}" in FAIL_ANALYSIS_PROMPT
        assert "{fail_context}" in FAIL_ANALYSIS_PROMPT
        assert "{step_annotations}" in FAIL_ANALYSIS_PROMPT

    def test_prompt_format(self):
        """WHY: Template muss mit .format() korrekt befüllt werden können."""
        filled = FAIL_ANALYSIS_PROMPT.format(
            frame_count=5,
            fail_context="Test-Kontext",
            step_annotations="- Frame 1: test",
        )
        assert "5" in filled
        assert "Test-Kontext" in filled
        assert "Frame 1: test" in filled
