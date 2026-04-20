#!/usr/bin/env python3
# ================================================================================
# DATEI: test_fail_recorder.py
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
Tests für fail_recorder.py — Ring-Buffer Screenshot Recorder
================================================================================
WHY: Ohne Tests wissen wir nicht ob der Ring-Buffer korrekt evictet,
     ob Keyframe-Sampling gleichmäßig verteilt ist, ob Annotationen
     korrekt den letzten Frame treffen.
CONSEQUENCES: Jeder Recorder-Bug würde im Fail-Fall stumme Datenlosigkeit
     verursachen — Tests fangen das vorher ab.
================================================================================
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from fail_recorder import RecordedFrame, ScreenRingRecorder, save_keyframes_to_disk


# ============================================================================
# HILFSFUNKTIONEN
# ============================================================================
# WHY: Wiederverwendbare Test-Fixtures sparen Boilerplate und machen Tests lesbarer.


def _make_frame(ts: float, label: str = "", verdict: str = "") -> RecordedFrame:
    """Erzeugt einen Test-Frame mit minimalem PNG-Stub."""
    # 8 Bytes PNG-Header als Stub — reicht für Tests, kein echtes Bild nötig.
    return RecordedFrame(
        timestamp=ts,
        png_bytes=b"\x89PNG\r\n\x1a\n" + label.encode()[:50],
        step_label=label,
        vision_verdict=verdict,
    )


def _fill_recorder(recorder: ScreenRingRecorder, count: int, start_ts: float = 1000.0):
    # -------------------------------------------------------------------------
    # FUNKTION: _fill_recorder
    # PARAMETER: recorder: ScreenRingRecorder, count: int, start_ts: float = 1000.0
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
    """Füllt den Recorder-Buffer direkt (ohne async capture)."""
    for i in range(count):
        recorder._frames.append(_make_frame(start_ts + i, f"step_{i}", "PROCEED"))


# ============================================================================
# UNIT TESTS — RecordedFrame
# ============================================================================


class TestRecordedFrame:
    # ========================================================================
    # KLASSE: TestRecordedFrame
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """Tests für die RecordedFrame Dataclass."""

    def test_default_fields(self):
        """WHY: Defaults müssen leer sein, damit Frames ohne Annotation funktionieren."""
        f = RecordedFrame(timestamp=1.0, png_bytes=b"\x89PNG")
        assert f.step_label == ""
        assert f.vision_verdict == ""
        assert f.page_state == ""

    def test_all_fields_set(self):
        """WHY: Alle Felder müssen korrekt gesetzt werden können."""
        f = RecordedFrame(
            timestamp=42.0,
            png_bytes=b"data",
            step_label="click_weiter",
            vision_verdict="STOP",
            page_state="survey_active",
        )
        assert f.timestamp == 42.0
        assert f.png_bytes == b"data"
        assert f.step_label == "click_weiter"
        assert f.vision_verdict == "STOP"
        assert f.page_state == "survey_active"


# ============================================================================
# UNIT TESTS — ScreenRingRecorder
# ============================================================================


class TestScreenRingRecorder:
    # ========================================================================
    # KLASSE: TestScreenRingRecorder
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """Tests für den ScreenRingRecorder Ring-Buffer."""

    def test_init_defaults(self):
        """WHY: Default-Werte müssen sinnvoll sein (1fps, 120s Buffer)."""
        rec = ScreenRingRecorder()
        assert rec._fps == 1.0
        assert rec._buffer_seconds == 120.0
        assert rec.frame_count == 0
        assert rec._running is False

    def test_custom_init(self):
        """WHY: Custom fps/buffer müssen korrekt übernommen werden."""
        rec = ScreenRingRecorder(fps=2.0, buffer_seconds=60.0)
        assert rec._fps == 2.0
        assert rec._buffer_seconds == 60.0

    def test_frame_count_empty(self):
        """WHY: Leerer Buffer muss 0 Frames haben."""
        rec = ScreenRingRecorder()
        assert rec.frame_count == 0

    def test_frame_count_after_fill(self):
        """WHY: frame_count muss die tatsächliche Anzahl widerspiegeln."""
        rec = ScreenRingRecorder()
        _fill_recorder(rec, 10)
        assert rec.frame_count == 10

    def test_buffer_duration_empty(self):
        """WHY: Leerer Buffer hat Dauer 0."""
        rec = ScreenRingRecorder()
        assert rec.buffer_duration_seconds == 0.0

    def test_buffer_duration_single_frame(self):
        """WHY: Ein einzelner Frame hat auch Dauer 0 (keine Zeitspanne)."""
        rec = ScreenRingRecorder()
        _fill_recorder(rec, 1)
        assert rec.buffer_duration_seconds == 0.0

    def test_buffer_duration_multiple_frames(self):
        """WHY: Dauer muss der Differenz zwischen erstem und letztem Timestamp entsprechen."""
        rec = ScreenRingRecorder()
        _fill_recorder(rec, 10, start_ts=100.0)
        # Frames: 100, 101, 102, ..., 109 → Dauer = 9.0
        assert rec.buffer_duration_seconds == 9.0

    def test_clear(self):
        """WHY: clear() muss den Buffer komplett leeren (Speicher freigeben)."""
        rec = ScreenRingRecorder()
        _fill_recorder(rec, 20)
        assert rec.frame_count == 20
        rec.clear()
        assert rec.frame_count == 0

    # ---- ANNOTATE ----

    def test_annotate_last_frame(self):
        """WHY: annotate_last_frame muss den letzten Frame im Buffer annotieren."""
        rec = ScreenRingRecorder()
        _fill_recorder(rec, 5)
        rec.annotate_last_frame("click_submit", "PROCEED", "survey_active")
        last = list(rec._frames)[-1]
        assert last.step_label == "click_submit"
        assert last.vision_verdict == "PROCEED"
        assert last.page_state == "survey_active"

    def test_annotate_empty_buffer_no_crash(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_annotate_empty_buffer_no_crash
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Annotation auf leerem Buffer darf nicht crashen."""
        rec = ScreenRingRecorder()
        rec.annotate_last_frame("test", "PROCEED")  # Muss stillschweigend nichts tun.
        assert rec.frame_count == 0

    def test_annotate_does_not_affect_earlier_frames(self):
        """WHY: Annotation darf nur den LETZTEN Frame ändern, nicht ältere."""
        rec = ScreenRingRecorder()
        _fill_recorder(rec, 3)
        rec.annotate_last_frame("only_last", "STOP")
        frames = list(rec._frames)
        # Vorhergehende Frames behalten ihre Original-Labels
        assert frames[0].step_label == "step_0"
        assert frames[1].step_label == "step_1"
        assert frames[2].step_label == "only_last"
        assert frames[2].vision_verdict == "STOP"

    # ---- KEYFRAMES ----

    def test_get_keyframes_empty(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_get_keyframes_empty
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Leerer Buffer muss leere Liste zurückgeben."""
        rec = ScreenRingRecorder()
        assert rec.get_keyframes() == []

    def test_get_keyframes_fewer_than_n(self):
        """WHY: Wenn weniger Frames als N vorhanden, alle zurückgeben."""
        rec = ScreenRingRecorder()
        _fill_recorder(rec, 5)
        kf = rec.get_keyframes(n=12)
        assert len(kf) == 5

    def test_get_keyframes_exact_n(self):
        """WHY: Wenn genau N Frames vorhanden, alle zurückgeben."""
        rec = ScreenRingRecorder()
        _fill_recorder(rec, 12)
        kf = rec.get_keyframes(n=12)
        assert len(kf) == 12

    def test_get_keyframes_sampling(self):
        """WHY: Bei >N Frames müssen sie gleichmäßig verteilt gesampelt werden."""
        rec = ScreenRingRecorder()
        _fill_recorder(rec, 120, start_ts=0)
        kf = rec.get_keyframes(n=12)
        assert len(kf) == 12
        # Prüfe dass die Zeitstempel aufsteigend sind (gleichmäßig verteilt)
        timestamps = [f.timestamp for f in kf]
        assert timestamps == sorted(timestamps)
        # Erster Frame sollte nahe am Anfang sein
        assert kf[0].timestamp < 10
        # Letzter Frame sollte nahe am Ende sein
        assert kf[-1].timestamp > 100

    def test_get_last_n_frames(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_get_last_n_frames
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: get_last_n_frames muss die chronologisch letzten N Frames liefern."""
        rec = ScreenRingRecorder()
        _fill_recorder(rec, 20, start_ts=100)
        last5 = rec.get_last_n_frames(5)
        assert len(last5) == 5
        assert last5[0].timestamp == 115.0
        assert last5[-1].timestamp == 119.0

    def test_get_last_n_frames_fewer_available(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_get_last_n_frames_fewer_available
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Wenn weniger als N Frames da sind, alle zurückgeben."""
        rec = ScreenRingRecorder()
        _fill_recorder(rec, 3)
        last = rec.get_last_n_frames(10)
        assert len(last) == 3

    # ---- ASYNC START/STOP ----

    @pytest.mark.asyncio
    async def test_start_stop(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_start_stop
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: start() und stop() müssen ohne Fehler funktionieren."""
        rec = ScreenRingRecorder(fps=10.0)
        # Mock die Capture-Methode damit kein echter Screenshot gemacht wird
        rec._capture_frame = AsyncMock(return_value=b"\x89PNG_fake_data_here")
        await rec.start()
        assert rec._running is True
        assert rec._task is not None
        # Kurz warten damit mindestens ein Frame aufgenommen wird
        await asyncio.sleep(0.3)
        await rec.stop()
        assert rec._running is False
        # Mindestens 1 Frame sollte aufgenommen worden sein
        assert rec.frame_count >= 1

    @pytest.mark.asyncio
    async def test_double_start_no_duplicate_task(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_double_start_no_duplicate_task
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Zweimal start() darf keinen zweiten Task erzeugen."""
        rec = ScreenRingRecorder()
        rec._capture_frame = AsyncMock(return_value=b"\x89PNG")
        await rec.start()
        task1 = rec._task
        await rec.start()  # Zweiter Aufruf
        task2 = rec._task
        assert task1 is task2  # Gleicher Task
        await rec.stop()

    @pytest.mark.asyncio
    async def test_capture_failure_does_not_crash(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_capture_failure_does_not_crash
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Capture-Fehler dürfen den Recorder nie crashen."""
        rec = ScreenRingRecorder(fps=10.0)
        rec._capture_frame = AsyncMock(return_value=None)  # Simuliert Fehler
        await rec.start()
        await asyncio.sleep(0.3)
        await rec.stop()
        # Kein Frame aufgenommen (alle None), aber kein Crash
        assert rec.frame_count == 0


# ============================================================================
# UNIT TESTS — save_keyframes_to_disk
# ============================================================================


class TestSaveKeyframesToDisk:
    # ========================================================================
    # KLASSE: TestSaveKeyframesToDisk
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """Tests für die Disk-Speicherung von Keyframes."""

    def test_saves_frames(self, tmp_path: Path):
        """WHY: Keyframes müssen als PNG-Dateien gespeichert werden."""
        frames = [_make_frame(i, f"step_{i}") for i in range(3)]
        paths = save_keyframes_to_disk(frames, tmp_path, prefix="kf")
        assert len(paths) == 3
        for p in paths:
            assert p.exists()
            assert p.suffix == ".png"
            assert p.stat().st_size > 0

    def test_creates_directory(self, tmp_path: Path):
    # -------------------------------------------------------------------------
    # FUNKTION: test_creates_directory
    # PARAMETER: self, tmp_path: Path
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Nicht existierende Verzeichnisse müssen automatisch erstellt werden."""
        deep_dir = tmp_path / "a" / "b" / "c"
        frames = [_make_frame(1, "test")]
        paths = save_keyframes_to_disk(frames, deep_dir)
        assert deep_dir.exists()
        assert len(paths) == 1

    def test_empty_keyframes(self, tmp_path: Path):
    # -------------------------------------------------------------------------
    # FUNKTION: test_empty_keyframes
    # PARAMETER: self, tmp_path: Path
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Leere Keyframe-Liste muss leere Pfad-Liste zurückgeben."""
        paths = save_keyframes_to_disk([], tmp_path)
        assert paths == []

    def test_file_naming(self, tmp_path: Path):
        """WHY: Dateinamen müssen dem prefix_XX.png Schema folgen."""
        frames = [_make_frame(i) for i in range(3)]
        paths = save_keyframes_to_disk(frames, tmp_path, prefix="fail")
        names = [p.name for p in paths]
        assert names == ["fail_00.png", "fail_01.png", "fail_02.png"]
