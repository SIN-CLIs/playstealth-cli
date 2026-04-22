# playstealth_actions/reward_queue.py
import re
import json
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

STATE_DIR = Path(os.getenv("PLAYSTEALTH_STATE_DIR", ".playstealth_state"))
BLACKLIST_FILE = STATE_DIR / "blacklist.json"
QUEUE_FILE = STATE_DIR / "survey_queue.json"

DEFAULT_CONFIG = {
    "min_epm": 0.08,          # Mindest-€/Min
    "max_duration_min": 30,   # Max. Dauer in Minuten
    "blacklist_enabled": True,
    "priority_keywords": ["bonus", "premium", "high", "express", "schnell", "top"],
    "fallback_duration_min": 10.0,
    "fallback_reward": 0.50
}

def _load_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else []
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def _save_json(path: Path, data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def parse_reward(text: str) -> float:
    """Extrahiert numerischen Reward-Wert. Ignoriert Währungssymbole & Text."""
    if not text: return 0.0
    match = re.search(r'[\d]+[.,]?[\d]*', text.replace(',', '.'))
    return float(match.group()) if match else 0.0

def parse_duration(text: str) -> float:
    """Parst Dauer in Minuten. Handhabt Ranges ('5-10 min'), Tilde, Texte."""
    if not text: return 0.0
    nums = re.findall(r'\d+', text)
    if not nums: return 0.0
    if len(nums) >= 2:
        return (float(nums[0]) + float(nums[1])) / 2.0
    return float(nums[0])

def calculate_epm(reward: float, duration: float) -> float:
    if duration <= 0: return 0.0
    return round(reward / duration, 4)

def load_blacklist() -> List[Dict[str, Any]]:
    return _load_json(BLACKLIST_FILE, [])

def save_blacklist(data: List[Dict[str, Any]]):
    _save_json(BLACKLIST_FILE, data)

def add_to_blacklist(survey_id: str, title: str = "", reason: str = "manual"):
    bl = load_blacklist()
    if any(b.get("id") == survey_id for b in bl):
        return
    bl.append({
        "id": survey_id,
        "title_pattern": title[:80].lower(),
        "reason": reason,
        "added_at": datetime.now().isoformat()
    })
    save_blacklist(bl)

def is_blacklisted(survey: Dict[str, Any], blacklist: List[Dict[str, Any]]) -> bool:
    if not blacklist: return False
    s_id = survey.get("id", "")
    s_title = survey.get("title", "").lower()
    for b in blacklist:
        if b.get("id") and b["id"] == s_id:
            return True
        if b.get("title_pattern") and b["title_pattern"] in s_title:
            return True
    return False

def build_queue(surveys: List[Dict[str, Any]], config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Filtert, bewertet & sortiert Surveys nach €/Min + Priorität."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    blacklist = load_blacklist() if cfg.get("blacklist_enabled") else []
    queue = []

    for s in surveys:
        reward_val = parse_reward(s.get("reward", "")) or cfg["fallback_reward"]
        dur_val = parse_duration(s.get("duration", "")) or cfg["fallback_duration_min"]
        epm = calculate_epm(reward_val, dur_val)

        if epm < cfg["min_epm"]:
            continue
        if dur_val > cfg["max_duration_min"]:
            continue
        if is_blacklisted(s, blacklist):
            continue

        priority = 1
        title_lower = s.get("title", "").lower()
        if any(kw in title_lower for kw in cfg["priority_keywords"]):
            priority = 2

        queue.append({
            **s,
            "reward_val": reward_val,
            "duration_val": dur_val,
            "epm": epm,
            "priority": priority,
            "score": round(epm * priority, 4)
        })

    # Sortierung: Score absteigend, bei Gleichstand kürzere Dauer zuerst
    queue.sort(key=lambda x: (-x["score"], x["duration_val"]))
    _save_json(QUEUE_FILE, queue)
    return queue

def get_next_survey(queue: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """Holt & entfernt das Top-Element aus der Queue."""
    if queue is None:
        queue = _load_json(QUEUE_FILE, [])
    if not queue:
        return None
    next_s = queue.pop(0)
    _save_json(QUEUE_FILE, queue)
    return next_s
