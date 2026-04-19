# -*- coding: utf-8 -*-
"""
Unit-Tests für audio_handler.

WHY: Der Audio-Handler ist der einzige Entry-Point für Survey-Audio-Fragen.
     Wenn er bricht, verliert der Worker eine ganze Frage-Kategorie. Die Tests
     decken: Prompt-Formatierung, MIME-Handling, NIM-Stub, Fehlerpfade.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audio_handler import (
    AudioTranscript,
    _average_confidence,
    _download_audio,
    _estimate_duration_seconds,
    _guess_mime_from_suffix,
    _mime_to_suffix,
    transcribe_audio,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.get_event_loop().is_running() else asyncio.run(coro)


def test_prompt_block_empty_transcript():
    t = AudioTranscript(
        transcript="",
        language="de",
        confidence=0.0,
        duration_sec=3.2,
        model_used="nvidia/parakeet-tdt-0.6b-v2",
        source_url="https://ex.com/a.mp3",
    )
    block = t.to_prompt_block()
    assert "leerer Transcript" in block


def test_prompt_block_with_text():
    t = AudioTranscript(
        transcript="Kaufen Sie jetzt das neue Modell!",
        language="de",
        confidence=0.87,
        duration_sec=5.0,
        model_used="nvidia/canary-1b-flash",
        source_url="https://ex.com/a.mp3",
    )
    block = t.to_prompt_block()
    assert "Kaufen Sie jetzt" in block
    assert "canary-1b-flash" in block
    assert "Konfidenz: 0.87" in block


def test_prompt_block_with_error():
    t = AudioTranscript(
        transcript="",
        language="de",
        confidence=0.0,
        duration_sec=0.0,
        model_used="nvidia/parakeet-tdt-0.6b-v2",
        source_url="",
        error="HTTP 503",
    )
    block = t.to_prompt_block()
    assert "HTTP 503" in block
    assert "fehlgeschlagen" in block.lower()


def test_mime_suffix_roundtrip():
    for suffix, mime in (
        (".mp3", "audio/mpeg"),
        (".wav", "audio/wav"),
        (".ogg", "audio/ogg"),
        (".m4a", "audio/mp4"),
        (".opus", "audio/opus"),
    ):
        assert _guess_mime_from_suffix(suffix) == mime
        assert _mime_to_suffix(mime) == suffix


def test_estimate_duration_mp3():
    # 1 MB MP3 @ 128 kbps ≈ 62.5 sec
    fake = b"\x00" * 1_000_000
    dur = _estimate_duration_seconds(fake, "audio/mpeg")
    assert 50 < dur < 80


def test_estimate_duration_wav_much_shorter():
    fake = b"\x00" * 1_000_000
    mp3_dur = _estimate_duration_seconds(fake, "audio/mpeg")
    wav_dur = _estimate_duration_seconds(fake, "audio/wav")
    assert wav_dur < mp3_dur  # WAV hat höhere Bitrate → kürzere geschätzte Dauer


def test_download_audio_data_url(tmp_path):
    payload = b"FAKEAUDIO"
    encoded = base64.b64encode(payload).decode("ascii")
    url = f"data:audio/mpeg;base64,{encoded}"
    raw, mime = _download_audio(url, max_bytes=1_000_000)
    assert raw == payload
    assert mime == "audio/mpeg"


def test_download_audio_file_url(tmp_path):
    f = tmp_path / "clip.wav"
    f.write_bytes(b"RIFFfakewavdata")
    raw, mime = _download_audio(f"file://{f}", max_bytes=1_000_000)
    assert raw.startswith(b"RIFF")
    assert mime == "audio/wav"


def test_download_audio_too_large(tmp_path):
    f = tmp_path / "huge.mp3"
    f.write_bytes(b"X" * 10_000)
    try:
        _download_audio(f"file://{f}", max_bytes=1_000)
    except ValueError as e:
        assert "zu groß" in str(e)
    else:
        raise AssertionError("Expected ValueError for oversized audio")


def test_average_confidence_empty():
    assert _average_confidence([]) == 0.0
    assert _average_confidence(None) == 0.0


def test_average_confidence_with_logprob():
    segs = [{"avg_logprob": -0.1}, {"avg_logprob": -0.2}]
    conf = _average_confidence(segs)
    # exp(-0.1)≈0.905, exp(-0.2)≈0.819, avg≈0.862
    assert 0.8 < conf < 0.92


def test_transcribe_without_api_key_returns_error():
    result = asyncio.run(
        transcribe_audio("https://example.com/a.mp3", nvidia_api_key="")
    )
    assert result.error
    assert "NVIDIA_API_KEY" in result.error
    assert result.transcript == ""


def test_transcribe_with_bad_url_returns_error():
    # Verwendet einen nicht-existenten Host → Download-Fehler
    result = asyncio.run(
        transcribe_audio(
            "http://127.0.0.1:1/nonexistent.mp3",
            nvidia_api_key="fake-key",
            timeout=2,
        )
    )
    assert result.error
    assert result.transcript == ""


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
