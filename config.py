#!/usr/bin/env python3
# ================================================================================
# DATEI: config.py
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


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_env_file(path: Path) -> dict[str, str]:
    """Liest einfache KEY=VALUE- und export KEY=VALUE-.env-Dateien."""
    if not path.is_file():
        return {}

    parsed: dict[str, str] = {}
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            parsed[key] = value
    except Exception:
        return {}
    return parsed


def ensure_saved_env_loaded() -> bool:
    """Lädt persistierte Worker-ENV-Werte aus gespeicherten .env-Dateien.

    WHY: Die HeyPiggy-Runs müssen ohne manuelles Nachfragen mit den lokal
    gespeicherten Zugangsdaten laufen. Priorität: explizite HEYPIGGY_ENV_FILE
    -> Repo-.env -> ~/.env. Setzt vorhandene Werte bewusst überschreibend.
    """
    if _is_truthy(os.environ.get("HEYPIGGY_DISABLE_SAVED_ENV")):
        return False

    repo_env = Path(__file__).resolve().parent / ".env"
    home_env = Path.home() / ".env"
    explicit_env_file = os.environ.get("HEYPIGGY_ENV_FILE", "").strip()

    candidates: list[Path] = [home_env, repo_env]
    if explicit_env_file:
        candidates.append(Path(explicit_env_file).expanduser())

    loaded_any = False
    for env_path in candidates:
        env_values = _parse_env_file(env_path)
        if not env_values:
            continue
        loaded_any = True
        for key, value in env_values.items():
            if key in {"HEYPIGGY_DISABLE_SAVED_ENV", "HEYPIGGY_ENV_FILE"}:
                continue
            os.environ[key] = value

    return loaded_any


@dataclass(frozen=True)
class BridgeConfig:
    """
    Bridge-Verbindungs-Konfiguration.
    WHY: Bridge-URLs ändern sich zwischen Dev/Prod/Test.
         Frozen dataclass verhindert versehentliche Mutation zur Laufzeit.
    CONSEQUENCES: Zentral konfiguriert, ENV-überschreibbar, typsicher.

    !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    ACHTUNG — KRITISCHE FALLE (2026-04-19 dokumentiert):
    Die Bridge läuft auf HuggingFace Spaces, NICHT lokal!
    NIEMALS `BRIDGE_MCP_URL="http://127.0.0.1:7777"` setzen!
    → Das erzeugt `Connection refused` und der Worker startet nicht.
    Die Chrome-Extension verbindet sich per WebSocket zu:
      wss://openjerro-opensin-bridge-mcp.hf.space/extension
    Der Worker verbindet sich per HTTP zu mcp_url (Default unten).
    Die Extension zeigt connect/disconnect-Loops (code=1005) in der
    Chrome Console — das ist normales HF-Spaces-WebSocket-Verhalten
    und KEIN Blocker für den Worker.
    !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    """

    mcp_url: str = "https://openjerro-opensin-bridge-mcp.hf.space/mcp"
    health_url: str = "https://openjerro-opensin-bridge-mcp.hf.space/health"
    connect_timeout: int = 600


@dataclass(frozen=True)
class VisionConfig:
    # ========================================================================
    # KLASSE: VisionConfig
    # ZWECK:
    # WICHTIG:
    # METHODEN:
    # ========================================================================

    """
    Vision-Model-Konfiguration (Legacy-Switch).
    WHY: `model` ist nur relevant falls VISION_BACKEND auf einen externen
         Vision-CLI-Provider gesetzt wird. Der Default-Laufzeit-Pfad ist
         VISION_BACKEND="auto" -> nimmt NVIDIA NIM mit NvidiaConfig.primary_model
         (meta/llama-3.2-11b-vision-instruct). Dieses Feld bleibt nur fuer
         experimentelle Provider-Switches bestehen.
    CONSEQUENCES: Wer NVIDIA nutzt (Default!), muss dieses Feld ignorieren.
    """

    # KEIN nvidia/ Prefix! Siehe NvidiaConfig Docstring für Details.
    model: str = "meta/llama-3.2-11b-vision-instruct"
    max_steps: int = 120
    max_retries: int = 5
    max_no_progress: int = 15
    max_click_escalations: int = 5
    cli_timeout: int = 180


@dataclass(frozen=True)
class NvidiaConfig:
    # ========================================================================
    # KLASSE: NvidiaConfig
    # ZWECK:
    # WICHTIG:
    # METHODEN:
    # ========================================================================

    """
    NVIDIA NIM API Konfiguration.
    WHY: API-Key, Modell-IDs und Fallback-Kette müssen konfigurierbar sein.
         Hardcoded API-URLs brechen bei Modellwechsel oder Region-Change.
    CONSEQUENCES: Alle NVIDIA-Parameter an einem Ort. Fallback-Kette explizit.

    !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    ACHTUNG — KRITISCHE FALLE (2026-04-19 dokumentiert):
    Modellnamen OHNE `nvidia/` Prefix!
    Die NVIDIA NIM API erwartet z.B. `meta/llama-3.2-11b-vision-instruct`,
    NICHT `nvidia/meta/llama-3.2-11b-vision-instruct`.
    Mit dem falschen Prefix bekommt man `404 Not Found` von der API.
    Das betrifft primary_model UND alle fallback_models.
    Ebenso VisionConfig.model (Legacy-Feld) — gleiche Konvention.
    Siehe auch: tests/test_config.py::test_worker_config_uses_typed_nested_defaults
    !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    """

    api_key: str = ""
    base_url: str = "https://integrate.api.nvidia.com/v1"
    # KEIN nvidia/ Prefix! Nur der Org/Model-Pfad wie von NIM erwartet.
    primary_model: str = "meta/llama-3.2-11b-vision-instruct"
    fallback_models: tuple[str, ...] = (
        # KEIN nvidia/ Prefix! Siehe Docstring oben.
        "microsoft/phi-3.5-vision-instruct",
        "microsoft/phi-3-vision-128k-instruct",
    )
    timeout: int = 120
    max_inline_bytes: int = 150_000


@dataclass(frozen=True)
class MediaConfig:
    # ========================================================================
    # KLASSE: MediaConfig
    # ZWECK:
    # WICHTIG:
    # METHODEN:
    # ========================================================================

    """
    Multi-Modal Media-Pipeline Konfiguration (Audio / Video / Bilder).
    WHY: Umfragen enthalten häufig <audio>/<video>/<img>-Fragen. Wir müssen
         steuern welche NVIDIA NIM Modelle für ASR und Video-Understanding
         genutzt werden, und welche Timeouts / Frame-Counts sinnvoll sind.
    CONSEQUENCES: Eine Instanz pro Worker-Run; ENV-überschreibbar.
    """

    audio_model: str = "nvidia/parakeet-tdt-0.6b-v2"
    audio_fallback_models: tuple[str, ...] = (
        "nvidia/canary-1b-flash",
        "nvidia/parakeet-ctc-1.1b",
    )
    video_model: str = "nvidia/cosmos-reason1-7b"
    video_fallback_models: tuple[str, ...] = (
        "nvidia/vita-1.5",
        "meta/llama-3.2-90b-vision-instruct",
    )
    audio_timeout: int = 90
    video_timeout: int = 180
    video_frame_count: int = 8
    language_hint: str = "de"
    enabled: bool = True
    max_audio_bytes: int = 20_000_000
    max_video_bytes: int = 80_000_000


@dataclass(frozen=True)
class QueueConfig:
    # ========================================================================
    # KLASSE: QueueConfig
    # ZWECK:
    # WICHTIG:
    # METHODEN:
    # ========================================================================

    """
    Multi-Survey Queue Konfiguration.
    WHY: Der Worker soll beliebig viele Surveys am Stück erledigen können.
         Cooldown + max_surveys verhindern Account-Flags und Endlos-Loops.
    """

    dashboard_url: str = "https://www.heypiggy.com/"
    max_surveys: int = 25
    cooldown_sec: float = 4.0
    cooldown_jitter_sec: float = 2.0
    autodetect: bool = True
    explicit_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class PersonaConfig:
    # ========================================================================
    # KLASSE: PersonaConfig
    # ZWECK:
    # WICHTIG:
    # METHODEN:
    # ========================================================================

    """
    Persona + Global-Brain-Konfiguration — Wahrheits-Backbone des Workers.

    WHY: Der Worker darf niemals lügen. Die Persona-Datei bestimmt welche
    Person er repräsentiert, das Answer-Log garantiert Konsistenz über
    Validation-Traps hinweg, und das OpenSIN Global Brain teilt Erkenntnisse
    mit anderen Agenten der Flotte.
    """

    username: str = ""  # leer = keine Persona, Worker läuft legacy
    profiles_dir: str = "profiles"
    answer_log_path: str = "artifacts/answer_history.jsonl"
    similarity_threshold: float = 0.78
    enabled: bool = True
    # Global Brain (OpenSIN)
    brain_url: str = "http://127.0.0.1:7070"
    brain_project_id: str = "heypiggy-survey-worker"
    brain_agent_id: str = "a2a-sin-worker-heypiggy"
    brain_enabled: bool = True
    brain_timeout_sec: float = 3.0


@dataclass(frozen=True)
class InfisicalConfig:
    # ========================================================================
    # KLASSE: InfisicalConfig
    # ZWECK:
    # WICHTIG:
    # METHODEN:
    # ========================================================================

    """Canonical Infisical sync settings.

    WHY: Secret delivery must be centrally controlled so agents never need to
    guess which vault, environment, or folder root to use.
    """

    enabled: bool = True
    domain: str = "https://eu.infisical.com"
    project_id: str = "fa7758b4-f84c-4297-966e-710056d531ef"
    environment: str = "dev"
    folder_root: str = "/opensin/a2a-sin-worker-heypiggy"
    auto_sync: bool = False
    sync_roots: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecorderConfig:
    # ========================================================================
    # KLASSE: RecorderConfig
    # ZWECK:
    # WICHTIG:
    # METHODEN:
    # ========================================================================

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
        # -------------------------------------------------------------------------
        # FUNKTION: ensure_dirs
        # PARAMETER: self
        # ZWECK:
        # WAS PASSIERT HIER:
        # WARUM DIESER WEG:
        # ACHTUNG:
        # -------------------------------------------------------------------------

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
    # ========================================================================
    # KLASSE: WorkerConfig
    # ZWECK:
    # WICHTIG:
    # METHODEN:
    # ========================================================================

    """
    Gesamt-Konfiguration für den HeyPiggy Vision Worker.
    WHY: Einzelner Entry-Point für die gesamte Worker-Konfiguration.
         Statt 15+ globale Variablen: ein typisiertes, validierbares Objekt.
    CONSEQUENCES: Worker-Code wird testbar (Config injizieren statt Globals patchen).
    """

    bridge: BridgeConfig = field(default_factory=BridgeConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    nvidia: NvidiaConfig = field(default_factory=NvidiaConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
    infisical: InfisicalConfig = field(default_factory=InfisicalConfig)
    recorder: RecorderConfig = field(default_factory=RecorderConfig)
    artifacts: ArtifactConfig = field(default_factory=ArtifactConfig)

    click_actions: tuple[str, ...] = (
        "click_element",
        "click_ref",
        "ghost_click",
        "vision_click",
        "click_coordinates",
    )
    opensin_v2: bool = False


def load_config_from_env() -> WorkerConfig:
    """
    Lädt eine WorkerConfig aus Environment-Variablen.
    WHY: Jeder Deployment-Kontext (Mac, HF VM, CI) hat andere Anforderungen.
    ENV-Variablen sind der Standard für containerisierte Konfiguration.
    CONSEQUENCES: Defaults greifen wenn ENV nicht gesetzt. Keine harten Crashes.
    """
    ensure_saved_env_loaded()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    fallback_models_env = os.environ.get("NVIDIA_FALLBACK_MODELS", "")
    fallback_models = NvidiaConfig.fallback_models
    if fallback_models_env.strip():
        fallback_models = tuple(
            model.strip() for model in fallback_models_env.split(",") if model.strip()
        )

    audio_fallback_env = os.environ.get("AUDIO_ASR_FALLBACK_MODELS", "")
    audio_fallback = MediaConfig.audio_fallback_models
    if audio_fallback_env.strip():
        audio_fallback = tuple(m.strip() for m in audio_fallback_env.split(",") if m.strip())

    video_fallback_env = os.environ.get("VIDEO_UNDERSTANDING_FALLBACK_MODELS", "")
    video_fallback = MediaConfig.video_fallback_models
    if video_fallback_env.strip():
        video_fallback = tuple(m.strip() for m in video_fallback_env.split(",") if m.strip())

    explicit_urls_env = os.environ.get("HEYPIGGY_SURVEY_URLS", "")
    explicit_urls: tuple[str, ...] = ()
    if explicit_urls_env.strip():
        explicit_urls = tuple(u.strip() for u in explicit_urls_env.split(",") if u.strip())

    opensin_v2 = os.environ.get("OPENSIN_V2", "0").strip().lower() in {"1", "true", "yes", "on"}
    infisical_sync_roots_env = os.environ.get("INFISICAL_SYNC_ROOTS", "")
    infisical_sync_roots: tuple[str, ...] = ()
    if infisical_sync_roots_env.strip():
        infisical_sync_roots = tuple(
            p.strip() for p in infisical_sync_roots_env.split(",") if p.strip()
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
            max_no_progress=int(os.environ.get("MAX_NO_PROGRESS", VisionConfig.max_no_progress)),
            max_click_escalations=int(
                os.environ.get("MAX_CLICK_ESCALATIONS", VisionConfig.max_click_escalations)
            ),
            cli_timeout=int(os.environ.get("VISION_CLI_TIMEOUT", VisionConfig.cli_timeout)),
        ),
        nvidia=NvidiaConfig(
            api_key=os.environ.get("NVIDIA_API_KEY", ""),
            base_url=os.environ.get("NVIDIA_NIM_BASE_URL", NvidiaConfig.base_url),
            primary_model=os.environ.get("NVIDIA_PRIMARY_MODEL", NvidiaConfig.primary_model),
            fallback_models=fallback_models,
            timeout=int(os.environ.get("NVIDIA_TIMEOUT", NvidiaConfig.timeout)),
            max_inline_bytes=int(
                os.environ.get("NVIDIA_MAX_INLINE_BYTES", NvidiaConfig.max_inline_bytes)
            ),
        ),
        media=MediaConfig(
            audio_model=os.environ.get("AUDIO_ASR_MODEL", MediaConfig.audio_model),
            audio_fallback_models=audio_fallback,
            video_model=os.environ.get("VIDEO_UNDERSTANDING_MODEL", MediaConfig.video_model),
            video_fallback_models=video_fallback,
            audio_timeout=int(os.environ.get("AUDIO_ASR_TIMEOUT", MediaConfig.audio_timeout)),
            video_timeout=int(
                os.environ.get("VIDEO_UNDERSTANDING_TIMEOUT", MediaConfig.video_timeout)
            ),
            video_frame_count=int(
                os.environ.get("VIDEO_FRAME_COUNT", MediaConfig.video_frame_count)
            ),
            language_hint=os.environ.get("MEDIA_LANGUAGE", MediaConfig.language_hint),
            enabled=os.environ.get("MEDIA_PIPELINE_ENABLED", "1") != "0",
            max_audio_bytes=int(
                os.environ.get("MEDIA_MAX_AUDIO_BYTES", MediaConfig.max_audio_bytes)
            ),
            max_video_bytes=int(
                os.environ.get("MEDIA_MAX_VIDEO_BYTES", MediaConfig.max_video_bytes)
            ),
        ),
        queue=QueueConfig(
            dashboard_url=os.environ.get("HEYPIGGY_DASHBOARD_URL", QueueConfig.dashboard_url),
            max_surveys=int(os.environ.get("HEYPIGGY_MAX_SURVEYS", QueueConfig.max_surveys)),
            cooldown_sec=float(os.environ.get("HEYPIGGY_COOLDOWN_SEC", QueueConfig.cooldown_sec)),
            cooldown_jitter_sec=float(
                os.environ.get("HEYPIGGY_COOLDOWN_JITTER", QueueConfig.cooldown_jitter_sec)
            ),
            autodetect=os.environ.get("HEYPIGGY_AUTODETECT", "1") != "0",
            explicit_urls=explicit_urls,
        ),
        persona=PersonaConfig(
            username=os.environ.get("HEYPIGGY_PERSONA", PersonaConfig.username),
            profiles_dir=os.environ.get("HEYPIGGY_PROFILES_DIR", PersonaConfig.profiles_dir),
            answer_log_path=os.environ.get("HEYPIGGY_ANSWER_LOG", PersonaConfig.answer_log_path),
            similarity_threshold=float(
                os.environ.get("HEYPIGGY_ANSWER_SIMILARITY", PersonaConfig.similarity_threshold)
            ),
            enabled=os.environ.get("HEYPIGGY_PERSONA_ENABLED", "1") != "0",
            brain_url=os.environ.get("BRAIN_URL", PersonaConfig.brain_url),
            brain_project_id=os.environ.get("BRAIN_PROJECT_ID", PersonaConfig.brain_project_id),
            brain_agent_id=os.environ.get("BRAIN_AGENT_ID", PersonaConfig.brain_agent_id),
            brain_enabled=os.environ.get("BRAIN_ENABLED", "1") != "0",
            brain_timeout_sec=float(
                os.environ.get("BRAIN_TIMEOUT_SEC", PersonaConfig.brain_timeout_sec)
            ),
        ),
        infisical=InfisicalConfig(
            enabled=os.environ.get("INFISICAL_ENABLED", "1") != "0",
            domain=os.environ.get("INFISICAL_DOMAIN", InfisicalConfig.domain),
            project_id=os.environ.get("INFISICAL_PROJECT_ID", InfisicalConfig.project_id),
            environment=os.environ.get("INFISICAL_ENV", InfisicalConfig.environment),
            folder_root=os.environ.get("INFISICAL_FOLDER_ROOT", InfisicalConfig.folder_root),
            auto_sync=os.environ.get("INFISICAL_AUTO_SYNC", "0") != "0",
            sync_roots=infisical_sync_roots,
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
        opensin_v2=opensin_v2,
    )
