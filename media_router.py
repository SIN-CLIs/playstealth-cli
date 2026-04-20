#!/usr/bin/env python3
# ================================================================================
# DATEI: media_router.py
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
Media Router — erkennt Audio/Video/Bild-Fragen und dispatcht zur Analyse
================================================================================
WHY: Der Vision-Worker hat bisher nur Screenshots gesehen — aber Umfragen
     enthalten oft <audio>, <video> und komplexe Bild-Fragen die nicht alleine
     aus einem Screenshot entschieden werden können (z.B. "Was hören Sie im
     folgenden Clip?", "Welche Aktion zeigt das Video?", "Welches Logo sehen Sie?").
CONSEQUENCES: Vor jedem Vision-Call scannen wir das DOM nach Media-Elementen.
     Wenn welche gefunden werden, laden wir sie herunter, schicken sie an die
     richtige Pipeline (Audio → audio_handler, Video → video_handler, Bild →
     Vision direkt) und injizieren die Ergebnisse in den Vision-Prompt.

INTEGRATION:
     Der Router ist zustandslos und rein funktional. Er nimmt einen
     `execute_bridge`-Callable und einen `tab_params`-Dict vom Worker entgegen.
     So bleibt die Tab-Bindungs-Invariante des Workers gewahrt.
================================================================================
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from audio_handler import AudioTranscript, transcribe_audio
from video_handler import VideoUnderstanding, understand_video


# ============================================================================
# DOM-SIDE DETECTION (wird als JS auf der Seite ausgeführt)
# ============================================================================

DETECT_MEDIA_JS = r"""
(function() {
  function absUrl(u) {
    if (!u) return '';
    try { return new URL(u, document.baseURI).href; } catch(e) { return u; }
  }
  function visibleRect(el) {
    var r = el.getBoundingClientRect();
    return r.width > 20 && r.height > 20;
  }
  var out = { audio: [], video: [], images: [], iframes: [] };

  // <audio>
  document.querySelectorAll('audio').forEach(function(el) {
    if (!visibleRect(el) && el.offsetParent === null) return;
    var src = el.currentSrc || el.src || '';
    if (!src) {
      var s = el.querySelector('source[src]');
      if (s) src = s.src;
    }
    if (!src) return;
    out.audio.push({
      src: absUrl(src),
      duration: el.duration || 0,
      paused: el.paused,
      ended: el.ended,
      muted: el.muted,
      id: el.id || '',
      selector: el.id ? '#' + el.id : 'audio'
    });
  });

  // <video>
  document.querySelectorAll('video').forEach(function(el) {
    if (!visibleRect(el) && el.offsetParent === null) return;
    var src = el.currentSrc || el.src || '';
    if (!src) {
      var s = el.querySelector('source[src]');
      if (s) src = s.src;
    }
    if (!src) return;
    out.video.push({
      src: absUrl(src),
      duration: el.duration || 0,
      paused: el.paused,
      ended: el.ended,
      muted: el.muted,
      poster: absUrl(el.poster || ''),
      width: el.videoWidth || 0,
      height: el.videoHeight || 0,
      id: el.id || '',
      selector: el.id ? '#' + el.id : 'video'
    });
  });

  // Fragerelevante <img> (in typischen Frage-Containern oder mit Alt-Text)
  var qSelectors = [
    '.question img', '[class*="question"] img', '[class*="survey"] img',
    '[class*="choice"] img', '[class*="option"] img', 'fieldset img',
    'label img', '.content img'
  ];
  var seen = new Set();
  qSelectors.forEach(function(sel) {
    document.querySelectorAll(sel).forEach(function(el) {
      if (!visibleRect(el)) return;
      var key = el.src + '|' + el.alt;
      if (seen.has(key)) return;
      seen.add(key);
      out.images.push({
        src: absUrl(el.currentSrc || el.src || ''),
        alt: el.alt || '',
        width: el.naturalWidth || 0,
        height: el.naturalHeight || 0
      });
    });
  });

  // Eingebettete Media-Iframes (YouTube, Vimeo, eigene Player)
  document.querySelectorAll('iframe').forEach(function(el) {
    if (!visibleRect(el)) return;
    var src = el.src || '';
    if (!src) return;
    var isMedia = /youtube|vimeo|brightcove|jwplayer|soundcloud|wistia|streamable/i.test(src);
    if (!isMedia) return;
    out.iframes.push({
      src: absUrl(src),
      title: el.title || '',
      platform: (src.match(/youtube|vimeo|brightcove|jwplayer|soundcloud|wistia|streamable/i) || ['?'])[0]
    });
  });

  return out;
})();
"""


# ============================================================================
# RESULT DATACLASSES
# ============================================================================


@dataclass(frozen=True)
class MediaSnapshot:
    # ========================================================================
    # KLASSE: MediaSnapshot
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """Zusammenfassung aller Media-Elemente die der Router gefunden hat."""

    audio_urls: tuple[str, ...] = ()
    video_urls: tuple[str, ...] = ()
    image_urls: tuple[str, ...] = ()
    embed_urls: tuple[str, ...] = ()
    audio_selectors: tuple[str, ...] = ()
    video_selectors: tuple[str, ...] = ()

    @property
    def has_media(self) -> bool:
        return bool(self.audio_urls or self.video_urls or self.image_urls or self.embed_urls)

    @property
    def summary_line(self) -> str:
        parts = []
        if self.audio_urls:
            parts.append(f"{len(self.audio_urls)}x audio")
        if self.video_urls:
            parts.append(f"{len(self.video_urls)}x video")
        if self.image_urls:
            parts.append(f"{len(self.image_urls)}x image")
        if self.embed_urls:
            parts.append(f"{len(self.embed_urls)}x embed")
        return ", ".join(parts) if parts else "none"


@dataclass(frozen=True)
class MediaAnalysis:
    # ========================================================================
    # KLASSE: MediaAnalysis
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """Vollständiges Analyse-Ergebnis nach Download + Verarbeitung."""

    snapshot: MediaSnapshot
    audio_transcripts: tuple[AudioTranscript, ...] = ()
    video_understandings: tuple[VideoUnderstanding, ...] = ()
    embed_notes: tuple[str, ...] = ()
    elapsed_sec: float = 0.0
    errors: tuple[str, ...] = ()

    @property
    def has_any(self) -> bool:
        return bool(
            self.audio_transcripts
            or self.video_understandings
            or self.embed_notes
            or self.snapshot.image_urls
        )

    def to_prompt_block(self) -> str:
        """Baut den kompletten Media-Kontext-Block für den Vision-Prompt."""
        if not self.has_any:
            return ""
        lines: list[str] = [
            "=" * 70,
            f"MEDIA-ANALYSE ({self.snapshot.summary_line}, benötigte {self.elapsed_sec:.1f}s):",
            "=" * 70,
        ]
        for idx, transcript in enumerate(self.audio_transcripts, 1):
            lines.append(f"\n[AUDIO-CLIP #{idx}]")
            lines.append(transcript.to_prompt_block())

        for idx, video in enumerate(self.video_understandings, 1):
            lines.append(f"\n[VIDEO-CLIP #{idx}]")
            lines.append(video.to_prompt_block())

        if self.snapshot.image_urls:
            lines.append(
                f"\n[BILDER IN FRAGE-KONTEXT] {len(self.snapshot.image_urls)} "
                f"Bild(er) im Screenshot sichtbar — analysiere sie direkt visuell "
                f"(Llama-3.2 Vision sieht sie bereits im Screenshot)."
            )

        for note in self.embed_notes:
            lines.append(f"\n[EMBED] {note}")

        if self.errors:
            lines.append("\n[MEDIA-FEHLER]")
            for err in self.errors[:5]:
                lines.append(f"- {err}")

        lines.append("=" * 70)
        return "\n".join(lines)


# ============================================================================
# ROUTER
# ============================================================================


BridgeCallable = Callable[[str, dict[str, Any]], Awaitable[Any]]


class MediaRouter:
    # ========================================================================
    # KLASSE: MediaRouter
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """
    Erkennt Media-Elemente auf der aktuellen Seite und dispatcht zur Analyse.

    WHY: Zentrale Stelle für die Multi-Modal-Logik — der Worker selbst bleibt
         schlank. Router ist instanzierbar, damit wir Cache + API-Key zentral halten.
    CONSEQUENCES: Eine Instanz pro Worker-Run reicht; er cachet bereits analysierte
         URLs (MD5-Hash) damit derselbe Clip nicht doppelt bezahlt wird.
    """

    def __init__(
        self,
        *,
        execute_bridge: BridgeCallable,
        tab_params_factory: Callable[[], dict[str, Any]],
        nvidia_api_key: str,
        audio_model: str | None = None,
        video_model: str | None = None,
        nim_base_url: str = "https://integrate.api.nvidia.com/v1",
        audio_timeout: int = 90,
        video_timeout: int = 180,
        frame_count: int = 8,
        language_hint: str = "de",
        audit: Callable[[str], None] | None = None,
    ) -> None:
        self._bridge = execute_bridge
        self._tab_params = tab_params_factory
        self._api_key = nvidia_api_key
        self._audio_model = audio_model
        self._video_model = video_model
        self._base_url = nim_base_url
        self._audio_timeout = audio_timeout
        self._video_timeout = video_timeout
        self._frame_count = frame_count
        self._language = language_hint
        self._audit = audit or (lambda msg: None)
        self._audio_cache: dict[str, AudioTranscript] = {}
        self._video_cache: dict[str, VideoUnderstanding] = {}

    async def scan_page(self) -> MediaSnapshot:
        """
        Führt die JS-Detektion aus und liefert eine Momentaufnahme.
        WHY: Ein einziger Bridge-Call pro Step — günstig genug um immer zu laufen.
        CONSEQUENCES: Bei Bridge-Fehler → leerer Snapshot, Worker läuft weiter.
        """
        try:
            result = await self._bridge(
                "execute_javascript",
                {"script": DETECT_MEDIA_JS, **self._tab_params()},
            )
        except Exception as e:
            self._audit(f"media_scan_error: {e}")
            return MediaSnapshot()

        data = result.get("result") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            return MediaSnapshot()

        audio_items = data.get("audio") or []
        video_items = data.get("video") or []
        image_items = data.get("images") or []
        iframe_items = data.get("iframes") or []

        audio_urls = tuple(
            str(i.get("src")) for i in audio_items if isinstance(i, dict) and i.get("src")
        )
        video_urls = tuple(
            str(i.get("src")) for i in video_items if isinstance(i, dict) and i.get("src")
        )
        image_urls = tuple(
            str(i.get("src")) for i in image_items if isinstance(i, dict) and i.get("src")
        )
        embed_urls = tuple(
            str(i.get("src")) for i in iframe_items if isinstance(i, dict) and i.get("src")
        )
        audio_selectors = tuple(
            str(i.get("selector", "audio")) for i in audio_items if isinstance(i, dict)
        )
        video_selectors = tuple(
            str(i.get("selector", "video")) for i in video_items if isinstance(i, dict)
        )

        return MediaSnapshot(
            audio_urls=audio_urls,
            video_urls=video_urls,
            image_urls=image_urls,
            embed_urls=embed_urls,
            audio_selectors=audio_selectors,
            video_selectors=video_selectors,
        )

    async def analyze(self, snapshot: MediaSnapshot) -> MediaAnalysis:
        """
        Analysiert alle gefundenen Audio-/Video-Assets parallel.
        WHY: Parallele Downloads + parallele NIM-Calls sparen bei multiplen Clips
             massiv Zeit; die Bridge-Calls laufen unabhängig voneinander.
        CONSEQUENCES: Caching über URL-Hash verhindert Doppelarbeit bei repetitiven
             Survey-Fragen (gleicher Clip, mehrere Folgefragen).
        """
        if not snapshot.has_media:
            return MediaAnalysis(snapshot=snapshot, elapsed_sec=0.0)

        start = time.monotonic()
        audio_tasks = [self._analyze_audio(u) for u in snapshot.audio_urls]
        video_tasks = [self._analyze_video(u) for u in snapshot.video_urls]

        audio_results: list[AudioTranscript] = []
        video_results: list[VideoUnderstanding] = []
        errors: list[str] = []

        if audio_tasks:
            settled = await asyncio.gather(*audio_tasks, return_exceptions=True)
            for res in settled:
                if isinstance(res, AudioTranscript):
                    audio_results.append(res)
                else:
                    errors.append(f"audio: {res}")

        if video_tasks:
            settled = await asyncio.gather(*video_tasks, return_exceptions=True)
            for res in settled:
                if isinstance(res, VideoUnderstanding):
                    video_results.append(res)
                else:
                    errors.append(f"video: {res}")

        embed_notes: list[str] = []
        for url in snapshot.embed_urls:
            embed_notes.append(
                f"Eingebettetes Media (YouTube/Vimeo/etc.): {url} — "
                f"nicht direkt analysierbar, nutze Screenshot-Kontext + Captions."
            )

        elapsed = round(time.monotonic() - start, 2)
        self._audit(
            f"media_analyzed: audio={len(audio_results)} video={len(video_results)} "
            f"embeds={len(embed_notes)} in {elapsed}s"
        )

        return MediaAnalysis(
            snapshot=snapshot,
            audio_transcripts=tuple(audio_results),
            video_understandings=tuple(video_results),
            embed_notes=tuple(embed_notes),
            elapsed_sec=elapsed,
            errors=tuple(errors),
        )

    async def scan_and_analyze(self) -> MediaAnalysis:
        """Convenience: scan_page() + analyze() in einem Aufruf."""
        import os

        snap = await self.scan_page()
        if not snap.has_media:
            # Media-Bypass (Issue #59): wenn SKIP_MEDIA_IF_NOT_FOUND=1 und kein
            # Media auf der Seite — sofort zurück ohne NVIDIA-API-Call.
            # WHY: Spart 200-800ms pro Schritt (kein HTTP-Call) wenn Surveys keine
            #      Audio/Video-Fragen haben — das ist der häufigste Fall.
            # CONSEQUENCES: Bei echter Audio/Video-Frage: scan_page() findet die
            #               Elemente und has_media=True → normaler Analyse-Pfad.
            if os.environ.get("SKIP_MEDIA_IF_NOT_FOUND", "1") != "0":
                return MediaAnalysis(snapshot=snap, elapsed_sec=0.0)
            return MediaAnalysis(snapshot=snap, elapsed_sec=0.0)
        return await self.analyze(snap)

    async def ensure_media_playing(self, snapshot: MediaSnapshot) -> None:
        """
        Startet pausierte <audio>/<video> Elemente damit der User-Flow stimmt.
        WHY: Manche Surveys gating die "Weiter"-Buttons bis der Clip
             mindestens einmal abgespielt wurde.
        CONSEQUENCES: Wir triggern .play() via JS; bei Auto-Play-Blockierung
             ist das ein No-Op, der Worker merkt es am nächsten Screenshot.
        """
        if not (snapshot.audio_urls or snapshot.video_urls):
            return

        js = r"""
        (function() {
          var triggered = [];
          document.querySelectorAll('audio, video').forEach(function(el) {
            try {
              if (el.paused && !el.ended) {
                el.muted = false;
                var p = el.play();
                if (p && p.catch) { p.catch(function(){}); }
                triggered.push(el.tagName + (el.id ? '#' + el.id : ''));
              }
            } catch(e) {}
          });
          return triggered;
        })();
        """
        try:
            await self._bridge(
                "execute_javascript",
                {"script": js, **self._tab_params()},
            )
            self._audit(f"media_play_triggered")
        except Exception as e:
            self._audit(f"media_play_error: {e}")

    # ------------------------------------------------------------------
    # Per-URL Analyse (mit Cache)
    # ------------------------------------------------------------------

    async def _analyze_audio(self, url: str) -> AudioTranscript:
        key = hashlib.md5(url.encode("utf-8")).hexdigest()
        if key in self._audio_cache:
            return self._audio_cache[key]
        transcript = await transcribe_audio(
            url,
            nvidia_api_key=self._api_key,
            model=self._audio_model,
            nim_base_url=self._base_url,
            timeout=self._audio_timeout,
            language_hint=self._language,
        )
        self._audio_cache[key] = transcript
        return transcript

    async def _analyze_video(self, url: str) -> VideoUnderstanding:
        key = hashlib.md5(url.encode("utf-8")).hexdigest()
        if key in self._video_cache:
            return self._video_cache[key]

        # Audio-Transcriber als Dependency injizieren — der Video-Handler nutzt ihn
        # um die Tonspur zu transkribieren.
        async def _audio_fn(local_url: str) -> AudioTranscript:
            return await transcribe_audio(
                local_url,
                nvidia_api_key=self._api_key,
                model=self._audio_model,
                nim_base_url=self._base_url,
                timeout=self._audio_timeout,
                language_hint=self._language,
            )

        understanding = await understand_video(
            url,
            nvidia_api_key=self._api_key,
            model=self._video_model,
            nim_base_url=self._base_url,
            timeout=self._video_timeout,
            frame_count=self._frame_count,
            audio_transcriber=_audio_fn,
        )
        self._video_cache[key] = understanding
        return understanding
