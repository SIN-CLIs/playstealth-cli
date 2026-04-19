# A2A Card: A2A-SIN-Worker-heypiggy

## Identity
- **Name:** A2A-SIN-Worker-heypiggy
- **Slug:** a2a-sin-worker-heypiggy
- **Type:** Worker
- **Team:** Team Worker
- **Manager:** A2A-SIN-Team-Worker

## Capabilities
- survey_completion
- autonomous_operation
- multi_survey_queue          # erledigt N Surveys am Stueck ohne Reset
- audio_question_handling     # NVIDIA Parakeet ASR fuer Hoer-Fragen
- video_question_handling     # NVIDIA Cosmos-Reason fuer Video-Fragen
- image_question_handling     # Llama-3.2 Vision direkt auf Screenshot
- auto_resume_on_crash
- captcha_handling
- fail_replay_learning
- matrix_grid_handling        # Likert/Rating-Tabellen zeilenweise
- slider_range_handling       # input[type=range] via dispatched events
- required_field_validation   # Pflichtfelder pre-submit-check
- infinite_spinner_recovery   # automatischer Reload bei Endlos-Ladern
- loop_detection_escalation   # Same-Question-Loop bricht durch
- native_select_handling      # echte HTML select via JS value+change
- human_typing_cadence        # 40-180ms Jitter pro Zeichen
- eur_totalizer               # Reward-Banner werden aggregiert
- session_persistence_cross_run # Cookies+LocalStorage 72h Cache
- panel_specific_overrides    # PureSpectrum/Dynata/Sapio/Cint/Lucid
- brain_backed_skip_filter    # bekannte DQ-URLs werden uebersprungen

## Models
- **Primary Vision:** nvidia/meta/llama-3.2-11b-vision-instruct (NVIDIA NIM)
- **Vision Fallback:** nvidia/microsoft/phi-3.5-vision-instruct, nvidia/microsoft/phi-3-vision-128k-instruct
- **Audio ASR:** nvidia/parakeet-tdt-0.6b-v2 (Fallback: canary-1b-flash, parakeet-ctc-1.1b)
- **Video Understanding:** nvidia/cosmos-reason1-7b (Fallback: vita-1.5, llama-3.2-90b-vision)

## Media-Pipeline
- Audio-Fragen: automatisches Playback + Transkription + Injection in Vision-Prompt
- Video-Fragen: Frame-Sampling + NIM Video-Understanding + Audio-Track-Transkription
- Bilder: direkt durch Llama-3.2 Vision (bereits im Screenshot)
- URL-basiertes Caching um doppelte NIM-Calls zu vermeiden

## Multi-Survey Queue
- Auto-Detect: "Naechste Umfrage" / "Next Survey" Buttons werden erkannt
- Explicit-Liste via `HEYPIGGY_SURVEY_URLS` ENV (kommasepariert)
- Max-Limit via `HEYPIGGY_MAX_SURVEYS` (default 25)
- Cooldown mit Jitter zwischen Surveys (Anti-Flag)
- Pro-Survey Gate-Reset: jede Survey startet mit frischem Step-Budget

## Endpoints
- **Health:** /health
- **Card:** /.well-known/agent-card.json
- **A2A:** /a2a/v1

## Marketplace
- **Pricing:** subscription ($29.99/mo)
- **Category:** worker

---
*Generated: 2026-04-14 | Config System v5*
