"""
Tests fuer media_router.py — prueft Snapshot-Erkennung, Playback-Trigger,
URL-Caching und Prompt-Block-Rendering.

Passt zur echten API in media_router.py:
- scan_page() nutzt `self._bridge("execute_javascript", ...)` und erwartet
  ein dict mit {"result": {"audio": [...], "video": [...], "images": [...],
  "iframes": [...]}}.
- MediaRouter cached per md5(url) in `_audio_cache` / `_video_cache`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from audio_handler import AudioTranscript
from media_router import MediaRouter, MediaSnapshot
from video_handler import VideoUnderstanding


@pytest.fixture
def router():
    execute_bridge = AsyncMock()
    r = MediaRouter(
        execute_bridge=execute_bridge,
        tab_params_factory=lambda: {"tab_id": "t1"},
        nvidia_api_key="key",
        audio_model="nvidia/parakeet-tdt-0.6b-v2",
        video_model="nvidia/cosmos-reason1-7b",
        nim_base_url="https://integrate.api.nvidia.com/v1",
    )
    return r


def _bridge_result(**kwargs):
    """Baut ein dict wie execute_javascript es zurueckgibt."""
    return {
        "result": {
            "audio": kwargs.get("audio", []),
            "video": kwargs.get("video", []),
            "images": kwargs.get("images", []),
            "iframes": kwargs.get("iframes", []),
        }
    }


@pytest.mark.asyncio
async def test_scan_empty_page(router):
    router._bridge.return_value = _bridge_result()
    snap = await router.scan_page()
    assert not snap.has_media
    assert snap.audio_urls == ()
    assert snap.video_urls == ()
    assert snap.image_urls == ()


@pytest.mark.asyncio
async def test_scan_detects_audio_video_images(router):
    router._bridge.return_value = _bridge_result(
        audio=[{"src": "https://cdn.example.com/q1.mp3", "selector": "audio"}],
        video=[{"src": "https://cdn.example.com/clip.mp4", "selector": "video"}],
        images=[{"src": "https://cdn.example.com/logo.png"}],
    )
    snap = await router.scan_page()
    assert snap.has_media
    assert "https://cdn.example.com/q1.mp3" in snap.audio_urls
    assert "https://cdn.example.com/clip.mp4" in snap.video_urls
    assert "https://cdn.example.com/logo.png" in snap.image_urls


@pytest.mark.asyncio
async def test_scan_handles_bridge_error(router):
    """Bridge-Fehler darf den Worker nicht crashen — leerer Snapshot ist der Vertrag."""
    router._bridge.side_effect = RuntimeError("bridge dead")
    snap = await router.scan_page()
    assert not snap.has_media


@pytest.mark.asyncio
async def test_scan_handles_malformed_result(router):
    """Wenn das JS ein Objekt ohne result-Key zurueckgibt — defensiv bleiben."""
    router._bridge.return_value = {"unexpected": "shape"}
    snap = await router.scan_page()
    assert not snap.has_media


@pytest.mark.asyncio
async def test_scan_filters_items_without_src(router):
    """Items ohne src-Feld sollen nicht in die URL-Liste wandern."""
    router._bridge.return_value = _bridge_result(
        audio=[{"selector": "audio"}, {"src": "https://ok.com/a.mp3"}],
    )
    snap = await router.scan_page()
    assert snap.audio_urls == ("https://ok.com/a.mp3",)


@pytest.mark.asyncio
async def test_ensure_media_playing_no_op_when_empty(router):
    snap = MediaSnapshot()
    await router.ensure_media_playing(snap)
    # Bridge wurde NICHT aufgerufen weil nichts abzuspielen war
    router._bridge.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_media_playing_triggers_js(router):
    router._bridge.return_value = {"result": {"triggered": 2}}
    snap = MediaSnapshot(audio_urls=("a.mp3",), video_urls=("v.mp4",))
    await router.ensure_media_playing(snap)
    router._bridge.assert_called_once()
    call_args = router._bridge.call_args
    assert call_args[0][0] == "execute_javascript"


@pytest.mark.asyncio
async def test_analyze_no_media_returns_empty(router):
    snap = MediaSnapshot()
    analysis = await router.analyze(snap)
    assert analysis.audio_transcripts == ()
    assert analysis.video_understandings == ()
    assert analysis.to_prompt_block() == ""


@pytest.mark.asyncio
async def test_analyze_uses_audio_cache(router, monkeypatch):
    """Dieselbe URL darf nicht zweimal transkribiert werden — NIM-Calls sind teuer."""
    call_count = {"n": 0}

    async def fake_transcribe(url, **kwargs):
        call_count["n"] += 1
        return AudioTranscript(
            transcript="Hallo Welt", language="de", confidence=0.9,
            duration_sec=3.0, model_used="test", source_url=url,
        )

    monkeypatch.setattr("media_router.transcribe_audio", fake_transcribe)

    snap = MediaSnapshot(audio_urls=("https://cdn.example.com/q1.mp3",))
    await router.analyze(snap)
    await router.analyze(snap)
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_analyze_handles_partial_failure(router, monkeypatch):
    """Ein Fehler bei Video darf den Audio-Erfolg nicht ausloeschen."""
    async def fake_transcribe(url, **kwargs):
        return AudioTranscript(
            transcript="Kaufen Sie Coca Cola", language="de", confidence=0.9,
            duration_sec=4.0, model_used="test", source_url=url,
        )

    async def fake_understand(url, **kwargs):
        raise RuntimeError("NIM 500")

    monkeypatch.setattr("media_router.transcribe_audio", fake_transcribe)
    monkeypatch.setattr("media_router.understand_video", fake_understand)

    snap = MediaSnapshot(
        audio_urls=("https://cdn.example.com/a.mp3",),
        video_urls=("https://cdn.example.com/v.mp4",),
    )
    analysis = await router.analyze(snap)
    assert len(analysis.audio_transcripts) == 1
    assert len(analysis.video_understandings) == 0
    assert len(analysis.errors) == 1


@pytest.mark.asyncio
async def test_prompt_block_includes_transcripts(router, monkeypatch):
    async def fake_transcribe(url, **kwargs):
        return AudioTranscript(
            transcript="Kaufen Sie Coca Cola", language="de", confidence=0.9,
            duration_sec=4.0, model_used="nvidia/parakeet-tdt-0.6b-v2",
            source_url=url,
        )

    monkeypatch.setattr("media_router.transcribe_audio", fake_transcribe)

    snap = MediaSnapshot(audio_urls=("https://cdn.example.com/ad.mp3",))
    analysis = await router.analyze(snap)
    block = analysis.to_prompt_block()
    assert "Coca Cola" in block
    assert len(block) > 0


@pytest.mark.asyncio
async def test_snapshot_has_any_detection():
    assert not MediaSnapshot().has_media
    assert MediaSnapshot(audio_urls=("x",)).has_media
    assert MediaSnapshot(video_urls=("x",)).has_media
    assert MediaSnapshot(image_urls=("x",)).has_media
    assert MediaSnapshot(embed_urls=("x",)).has_media
