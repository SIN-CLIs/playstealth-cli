#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Fail-Replay Ring Buffer Recorder
================================================================================
WHY: Wenn ein Survey-Run fehlschlägt, haben wir nur einzelne Screenshots.
     Ein Ring-Buffer hält die letzten 120 Sekunden als Frame-Sequenz vor —
     bei Fail werden 12 Keyframes extrahiert und an NVIDIA Llama-90B gesendet.
CONSEQUENCES: Läuft async im Hintergrund, NULL Performance-Impact im Happy-Path.
     Bei Fail: sofortige Root-Cause-Analyse via Multi-Frame Vision.
================================================================================
"""

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class RecordedFrame:
    """
    Ein einzelner aufgenommener Frame mit Zeitstempel und Step-Annotation.
    WHY: Jeder Frame braucht Kontext damit die Video-Analyse weiß was gerade passierte.
    CONSEQUENCES: step_label + vision_verdict ermöglichen exakte Fail-Zuordnung.
    """

    timestamp: float
    png_bytes: bytes
    step_label: str = ""  # z.B. "step_12_click_weiter"
    vision_verdict: str = ""  # z.B. "PROCEED", "STOP", "RETRY"
    page_state: str = ""  # z.B. "survey_active", "dashboard"


class ScreenRingRecorder:
    """
    Nimmt Screenshots auf und hält die letzten N Sekunden als Ring-Buffer.
    WHY: Ring-Buffer stellt sicher dass wir nie mehr als nötig speichern.
         Im Happy-Path werden alte Frames automatisch evicted.
         Im Fail-Path werden die letzten Frames als Keyframes extrahiert.
    CONSEQUENCES: Konstanter Speicherverbrauch (~120 Frames * ~50KB = ~6MB).
    """

    def __init__(
        self,
        fps: float = 1.0,
        buffer_seconds: float = 120.0,
        screenshot_method: str = "screencapture",
    ):
        # WHY fps=1.0: Schneller brauchen wir nicht — Survey-Steps dauern 3-5s.
        # Mehr als 1fps wäre Verschwendung.
        self._fps = fps
        self._buffer_seconds = buffer_seconds
        self._screenshot_method = screenshot_method
        self._frames: deque[RecordedFrame] = deque()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # Zähler für Fehlschläge bei Screenshot-Capture (exponentieller Backoff)
        self._consecutive_capture_fails = 0

    async def start(self):
        """
        Startet den Ring-Buffer-Recorder als Background-Task.
        WHY: Muss async laufen damit der Worker-Hauptloop nicht blockiert wird.
        CONSEQUENCES: Task läuft bis stop() oder Worker-Ende.
        """
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._record_loop())

    async def stop(self):
        """
        Stoppt den Recorder sauber.
        WHY: Bei Worker-Ende muss der Background-Task beendet werden.
        CONSEQUENCES: Bestehende Frames bleiben im Buffer für finale Extraktion.
        """
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _record_loop(self):
        """
        Hauptloop: nimmt alle 1/fps Sekunden einen Screenshot auf.
        WHY: Kontinuierliche Aufnahme damit bei Fail immer Kontext da ist.
        CONSEQUENCES: Alte Frames werden automatisch aus dem Buffer geworfen.
        """
        interval = 1.0 / self._fps
        while self._running:
            try:
                frame_bytes = await self._capture_frame()
                if frame_bytes:
                    self._frames.append(
                        RecordedFrame(timestamp=time.time(), png_bytes=frame_bytes)
                    )
                    self._consecutive_capture_fails = 0
                    # Alte Frames evicten — nur die letzten buffer_seconds behalten
                    cutoff = time.time() - self._buffer_seconds
                    while self._frames and self._frames[0].timestamp < cutoff:
                        self._frames.popleft()
                else:
                    self._consecutive_capture_fails += 1
            except asyncio.CancelledError:
                break
            except Exception:
                # WHY: Recorder-Fehler darf NIEMALS den Worker crashen.
                # CONSEQUENCES: Stille Fortsetzung, Frame wird übersprungen.
                self._consecutive_capture_fails += 1

            # Adaptives Interval: Bei wiederholten Fehlern langsamer werden
            backoff = min(self._consecutive_capture_fails * 2.0, 30.0)
            await asyncio.sleep(interval + backoff)

    async def _capture_frame(self) -> Optional[bytes]:
        """
        Macht einen Screenshot via screencapture (macOS) oder Fallback.
        WHY: screencapture ist der schnellste Weg auf macOS — kein Browser-Roundtrip.
        CONSEQUENCES: Gibt None zurück wenn Capture fehlschlägt (kein Crash).
        """
        if self._screenshot_method == "screencapture":
            return await self._capture_screencapture()
        return None

    async def _capture_screencapture(self) -> Optional[bytes]:
        """
        macOS screencapture nach stdout — kein Temp-File nötig.
        WHY: Direkt nach stdout pipen vermeidet Disk-I/O.
        CONSEQUENCES: Funktioniert nur auf macOS. Auf Linux/HF VM: anderer Fallback nötig.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "screencapture",
                "-x",
                "-t",
                "png",
                "/dev/stdout",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if stdout and len(stdout) > 100:
                return stdout
            return None
        except (asyncio.TimeoutError, FileNotFoundError, OSError):
            return None

    def annotate_last_frame(
        self,
        step_label: str,
        verdict: str = "",
        page_state: str = "",
    ):
        """
        Annotiert den letzten Frame mit Step-Kontext.
        WHY: Ohne Annotation weiß die Video-Analyse nicht welcher Step welcher Frame war.
        CONSEQUENCES: Nur der letzte Frame wird annotiert — ältere behalten ihren Kontext.
        """
        if self._frames:
            self._frames[-1].step_label = step_label
            self._frames[-1].vision_verdict = verdict
            self._frames[-1].page_state = page_state

    def get_keyframes(self, n: int = 12) -> list[RecordedFrame]:
        """
        Gibt N gleichmäßig verteilte Keyframes aus dem Buffer zurück.
        WHY: 12 Frames = guter Kompromiss zwischen Detail und NVIDIA-NIM-Limits (max 16 Bilder).
        CONSEQUENCES: Gleichmäßig verteilt statt nur die letzten N — zeigt den ganzen Verlauf.
        """
        frames = list(self._frames)
        if not frames:
            return []
        if len(frames) <= n:
            return frames
        # Gleichmäßig verteilt samplen
        step = len(frames) / n
        return [frames[int(i * step)] for i in range(n)]

    def get_last_n_frames(self, n: int = 5) -> list[RecordedFrame]:
        """
        Gibt die letzten N Frames zurück (chronologisch).
        WHY: Manchmal will man nur die Frames direkt vor dem Fail.
        CONSEQUENCES: Nützlich für schnelle "was ist gerade passiert?" Analyse.
        """
        frames = list(self._frames)
        return frames[-n:] if len(frames) >= n else frames

    @property
    def frame_count(self) -> int:
        """Anzahl der aktuell im Buffer gespeicherten Frames."""
        return len(self._frames)

    @property
    def buffer_duration_seconds(self) -> float:
        """Zeitspanne die der aktuelle Buffer abdeckt (in Sekunden)."""
        if len(self._frames) < 2:
            return 0.0
        return self._frames[-1].timestamp - self._frames[0].timestamp

    def clear(self):
        """
        Leert den Buffer komplett.
        WHY: Nach erfolgreichem Survey-Abschluss brauchen wir die alten Frames nicht mehr.
        CONSEQUENCES: Speicher wird sofort freigegeben.
        """
        self._frames.clear()


def save_keyframes_to_disk(
    keyframes: list[RecordedFrame],
    output_dir: str | Path,
    prefix: str = "frame",
) -> list[Path]:
    """
    Speichert Keyframes als PNG-Dateien auf Disk.
    WHY: Für manuelles Debugging und Box.com Upload brauchen wir Dateien.
    CONSEQUENCES: Erstellt output_dir falls nicht vorhanden.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, frame in enumerate(keyframes):
        fp = out / f"{prefix}_{i:02d}.png"
        fp.write_bytes(frame.png_bytes)
        paths.append(fp)
    return paths
