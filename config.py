#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Typed Worker Configuration — Zentrale Konfiguration für HeyPiggy Vision Worker
================================================================================
WHY: Alle Konfigurationswerte waren als nackte Modul-Globals verstreut.
     Typo in einem Variablennamen → stiller Bug. Keine Validierung.
     Keine Möglichkeit verschiedene Configs für Test/Prod zu laden.
CONSEQUENCES: Ein einziger typisierter Einstiegspunkt für alle Konfiguration.
     Validierung beim Start. ENV-Overrides für jeden Wert. Testbar.
================================================================================
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime


@dataclass(frozen=True)
class BridgeConfig:
    """
    Bridge-Verbindungs-Konfiguration.
    WHY: Bridge-URLs ändern sich zwischen Dev/Prod/Test.
         Frozen dataclass verhindert versehentliche Mutation zur Laufzeit.
    CONSEQUENCES: Zentral konfiguriert, ENV-überschreibbar, typsicher.
    """

    mcp_url: str = "https://openjerro-opensin-bridge-mcp.hf.space/mcp"
    health_url: str = "https://openjerro-opensin-bridge-mcp.hf.space/health"
    connect_timeout: int = 600


@dataclass(frozen=True)
class VisionConfig:
    """
    Vision-Model-Konfiguration.
    WHY: Vision-Model, Timeouts und Retry-Limits müssen zentral steuerbar sein.
         Verschiedene Modelle haben verschiedene Timeout-Anforderungen.
    CONSEQUENCES: Ein Ort für alle Vision-Parameter. ENV-Overrides möglich.
    """

    model: str = "google/antigravity-gemini-3-flash"
    max_steps: int = 120
    max_retries: int = 5
    max_no_progress: int = 15
    max_click_escalations: int = 5
    cli_timeout: int = 180


@dataclass(frozen=True)
class NvidiaConfig:
    """
    NVIDIA NIM API Konfiguration.
    WHY: API-Key, Modell-IDs und Fallback-Kette müssen konfigurierbar sein.
         Hardcoded API-URLs brechen bei Modellwechsel oder Region-Change.
    CONSEQUENCES: Alle NVIDIA-Parameter an einem Ort. Fallback-Kette explizit.
    """

    api_key: str = ""
    base_url: str = "https://integrate.api.nvidia.com/v1"
    primary_model: str = "nvidia/meta/llama-3.2-11b-vision-instruct"
    fallback_models: tuple[str, ...] = (
        "nvidia/microsoft/phi-3.5-vision-instruct",
        "nvidia/microsoft/phi-3-vision-128k-instruct",
    )
    timeout: int = 120
    max_inline_bytes: int = 150_000


@dataclass(frozen=True)
class RecorderConfig:
    """
    Fail-Replay Recorder Konfiguration.
    WHY: FPS und Buffer-Dauer sind Tradeoffs zwischen RAM und Abdeckung.
         Muss für Tests auf niedrige Werte setzbar sein.
    CONSEQUENCES: Konfigurierbar ohne Code-Änderung.
    """

    fps: float = 1.0
    buffer_seconds: float = 120.0
    keyframes_on_fail: int = 12


@dataclass(frozen=True)
class ArtifactConfig:
    """
    Artefakt-Verzeichnis-Konfiguration.
    WHY: /tmp/ ist auf HF VMs flüchtig, auf Mac persistent.
         Verzeichnisse müssen konfigurierbar und vorhersagbar sein.
    CONSEQUENCES: Alle Pfade von einem einzigen run_id abgeleitet.
    """

    run_id: str = ""
    base_dir: str = "/tmp"

    @property
    def artifact_dir(self) -> Path:
        return Path(self.base_dir) / f"heypiggy_run_{self.run_id}"

    @property
    def screenshot_dir(self) -> Path:
        return self.artifact_dir / "screenshots"

    @property
    def audit_dir(self) -> Path:
        return self.artifact_dir / "audit"

    @property
    def session_dir(self) -> Path:
        return self.artifact_dir / "sessions"

    def ensure_dirs(self):
        """Erstellt alle Artefakt-Verzeichnisse."""
        for d in [
            self.artifact_dir,
            self.screenshot_dir,
            self.audit_dir,
            self.session_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class WorkerConfig:
    """
    Gesamt-Konfiguration für den HeyPiggy Vision Worker.
    WHY: Einzelner Entry-Point für die gesamte Worker-Konfiguration.
         Statt 15+ globale Variablen: ein typisiertes, validierbares Objekt.
    CONSEQUENCES: Worker-Code wird testbar (Config injizieren statt Globals patchen).
    """

    bridge: BridgeConfig = field(default_factory=BridgeConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    nvidia: NvidiaConfig = field(default_factory=NvidiaConfig)
    recorder: RecorderConfig = field(default_factory=RecorderConfig)
    artifacts: ArtifactConfig = field(default_factory=ArtifactConfig)

    click_actions: tuple[str, ...] = (
        "click_element",
        "click_ref",
        "ghost_click",
        "vision_click",
        "click_coordinates",
    )


def load_config_from_env() -> WorkerConfig:
    """
    Lädt eine WorkerConfig aus Environment-Variablen.
    WHY: Jeder Deployment-Kontext (Mac, HF VM, CI) hat andere Anforderungen.
         ENV-Variablen sind der Standard für containerisierte Konfiguration.
    CONSEQUENCES: Defaults greifen wenn ENV nicht gesetzt. Keine harten Crashes.
    """
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    fallback_models_env = os.environ.get("NVIDIA_FALLBACK_MODELS", "")
    fallback_models = NvidiaConfig.fallback_models
    if fallback_models_env.strip():
        fallback_models = tuple(
            model.strip() for model in fallback_models_env.split(",") if model.strip()
        )

    return WorkerConfig(
        bridge=BridgeConfig(
            mcp_url=os.environ.get("BRIDGE_MCP_URL", BridgeConfig.mcp_url),
            health_url=os.environ.get("BRIDGE_HEALTH_URL", BridgeConfig.health_url),
            connect_timeout=int(
                os.environ.get("BRIDGE_CONNECT_TIMEOUT", BridgeConfig.connect_timeout)
            ),
        ),
        vision=VisionConfig(
            model=os.environ.get("VISION_MODEL", VisionConfig.model),
            max_steps=int(os.environ.get("MAX_STEPS", VisionConfig.max_steps)),
            max_retries=int(os.environ.get("MAX_RETRIES", VisionConfig.max_retries)),
            max_no_progress=int(
                os.environ.get("MAX_NO_PROGRESS", VisionConfig.max_no_progress)
            ),
            max_click_escalations=int(
                os.environ.get(
                    "MAX_CLICK_ESCALATIONS", VisionConfig.max_click_escalations
                )
            ),
            cli_timeout=int(
                os.environ.get("VISION_CLI_TIMEOUT", VisionConfig.cli_timeout)
            ),
        ),
        nvidia=NvidiaConfig(
            api_key=os.environ.get("NVIDIA_API_KEY", ""),
            base_url=os.environ.get("NVIDIA_NIM_BASE_URL", NvidiaConfig.base_url),
            primary_model=os.environ.get(
                "NVIDIA_PRIMARY_MODEL", NvidiaConfig.primary_model
            ),
            fallback_models=fallback_models,
            timeout=int(os.environ.get("NVIDIA_TIMEOUT", NvidiaConfig.timeout)),
            max_inline_bytes=int(
                os.environ.get("NVIDIA_MAX_INLINE_BYTES", NvidiaConfig.max_inline_bytes)
            ),
        ),
        recorder=RecorderConfig(
            fps=float(os.environ.get("RECORDER_FPS", RecorderConfig.fps)),
            buffer_seconds=float(
                os.environ.get("RECORDER_BUFFER_SECONDS", RecorderConfig.buffer_seconds)
            ),
            keyframes_on_fail=int(
                os.environ.get("RECORDER_KEYFRAMES", RecorderConfig.keyframes_on_fail)
            ),
        ),
        artifacts=ArtifactConfig(
            run_id=os.environ.get("HEYPIGGY_RUN_ID", run_id),
            base_dir=os.environ.get("HEYPIGGY_ARTIFACT_BASE", "/tmp"),
        ),
    )
