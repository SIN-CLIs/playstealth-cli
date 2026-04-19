# -*- coding: utf-8 -*-
"""
Unit-Tests für video_handler.
"""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from video_handler import (
    VideoUnderstanding,
    _download_video,
    _extract_json,
    _parse_video_response,
    understand_video,
)


def test_prompt_block_minimal():
    v = VideoUnderstanding(
        summary="Ein Mann trinkt Cola.",
        objects=("Mann", "Cola-Dose"),
        actions=("trinken",),
        brands=("Coca-Cola",),
        on_screen_text="Always Coca-Cola",
        spoken_transcript="Es ist eine Zeit für Freude.",
        duration_sec=12.0,
        frame_count=8,
        model_used="nvidia/cosmos-reason1-7b",
        source_url="https://ex.com/clip.mp4",
    )
    block = v.to_prompt_block()
    assert "Coca-Cola" in block
    assert "Mann" in block
    assert "trinken" in block
    assert "12.0s" in block


def test_prompt_block_with_error():
    v = VideoUnderstanding(
        summary="",
        objects=(),
        actions=(),
        brands=(),
        on_screen_text="",
        spoken_transcript="",
        duration_sec=0.0,
        frame_count=0,
        model_used="nvidia/cosmos-reason1-7b",
        source_url="",
        error="Timeout",
    )
    block = v.to_prompt_block()
    assert "Timeout" in block
    assert "fehlgeschlagen" in block.lower()


def test_extract_json_plain():
    text = '{"summary": "ok", "objects": ["a"]}'
    out = _extract_json(text)
    assert out == {"summary": "ok", "objects": ["a"]}


def test_extract_json_with_markdown():
    text = '```json\n{"summary": "ok"}\n```'
    out = _extract_json(text)
    assert out == {"summary": "ok"}


def test_extract_json_embedded():
    text = 'Blah blah {"summary": "wow"} footer'
    out = _extract_json(text)
    assert out == {"summary": "wow"}


def test_extract_json_invalid():
    assert _extract_json("no json here") is None
    assert _extract_json("") is None


def test_parse_video_response_happy():
    data = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"summary":"man runs","objects":["track"],'
                        '"actions":["run"],"brands":["Nike"],'
                        '"on_screen_text":"Just Do It","spoken_transcript":"go",'
                        '"duration_sec":10}'
                    )
                }
            }
        ]
    }
    parsed = _parse_video_response(data)
    assert parsed["summary"] == "man runs"
    assert parsed["brands"] == ["Nike"]
    assert parsed["duration_sec"] == 10.0


def test_parse_video_response_missing_choices():
    parsed = _parse_video_response({"choices": []})
    assert parsed["summary"] == ""
    assert parsed["objects"] == []


def test_download_video_data_url():
    payload = b"FAKEMP4" * 100
    encoded = base64.b64encode(payload).decode("ascii")
    url = f"data:video/mp4;base64,{encoded}"
    raw = _download_video(url, max_bytes=10_000)
    assert raw == payload


def test_download_video_file(tmp_path):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"MP4DATA")
    raw = _download_video(f"file://{f}", max_bytes=1000)
    assert raw == b"MP4DATA"


def test_download_video_too_large(tmp_path):
    f = tmp_path / "big.mp4"
    f.write_bytes(b"X" * 5000)
    try:
        _download_video(f"file://{f}", max_bytes=1000)
    except ValueError as e:
        assert "zu groß" in str(e)
    else:
        raise AssertionError("Expected ValueError")


def test_understand_without_api_key():
    result = asyncio.run(
        understand_video("https://example.com/v.mp4", nvidia_api_key="")
    )
    assert result.error
    assert "NVIDIA_API_KEY" in result.error


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
