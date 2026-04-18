#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Fail Report Generator + Publisher
================================================================================
WHY: Jeder Worker-Fail muss als GitHub Issue Comment dokumentiert werden.
     Ohne automatische Fail-Reports vergessen wir warum etwas versagte.
CONSEQUENCES: Vollständige Nachvollziehbarkeit aller Worker-Versagen.
     Reports enthalten: NVIDIA Video-Analyse + Keyframe-URLs + Step-Kontext.
================================================================================
"""

import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def generate_fail_report_markdown(
    analysis: dict,
    run_id: str,
    total_steps: int = 0,
    last_page_state: str = "",
    keyframe_urls: Optional[list[str]] = None,
) -> str:
    """
    Generiert einen Markdown Fail-Report aus der NVIDIA Video-Analyse.
    WHY: Strukturierter Report ermöglicht schnelles Debugging.
    CONSEQUENCES: Kann direkt als GitHub Issue Comment gepostet werden.
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    # Analyse-Felder extrahieren (mit sicheren Defaults)
    root_cause = analysis.get("root_cause", "Unbekannt")
    affected_step = analysis.get("affected_step", "N/A")
    fix_rec = analysis.get("fix_recommendation", "N/A")
    confidence = analysis.get("confidence_score", 0)
    frame_evidence = analysis.get("frame_evidence", "N/A")
    captcha = analysis.get("captcha_detected", False)
    timing = analysis.get("timing_issue", False)
    selector = analysis.get("selector_issue", False)
    loop = analysis.get("loop_detected", False)
    error = analysis.get("error", "")

    report = f"""## 🔴 HeyPiggy Worker Fail Report

**Run ID:** `{run_id}`
**Timestamp:** {timestamp}
**Total Steps:** {total_steps}
**Last Page State:** `{last_page_state}`

### Root Cause
> {root_cause}

### NVIDIA Video-Vision Analyse

| Feld | Wert |
|------|------|
| Betroffener Schritt | {affected_step} |
| Fix-Empfehlung | {fix_rec} |
| Confidence | {f"{confidence:.0%}" if isinstance(confidence, (int, float)) else confidence} |
| Frame-Beweis | {frame_evidence} |
| Captcha erkannt | {"✅ JA" if captcha else "❌ Nein"} |
| Timing-Problem | {"✅ JA" if timing else "❌ Nein"} |
| Selector-Problem | {"✅ JA" if selector else "❌ Nein"} |
| Loop erkannt | {"✅ JA" if loop else "❌ Nein"} |
"""

    if error:
        report += f"\n### ⚠️ Analyse-Fehler\n```\n{error}\n```\n"

    if keyframe_urls:
        report += "\n### Keyframes\n"
        for i, url in enumerate(keyframe_urls[:6]):
            report += f"- Frame {i + 1}: {url}\n"

    report += f"""
### Raw Analysis
```json
{json.dumps(analysis, indent=2, ensure_ascii=False)[:2000]}
```
"""
    return report


def post_github_issue_comment(
    repo: str,
    issue_number: int,
    comment_body: str,
) -> bool:
    """
    Postet einen Comment auf ein GitHub Issue via gh CLI.
    WHY: Fail-Reports müssen im Repo sichtbar sein — nicht nur in Logs.
    CONSEQUENCES: Gibt True zurück wenn erfolgreich, False sonst.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "comment",
                str(issue_number),
                "--repo",
                repo,
                "--body",
                comment_body[:65000],  # GitHub Limit
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def upload_to_box(file_path: str | Path) -> Optional[str]:
    """
    Lädt eine Datei zu Box.com hoch via A2A-SIN-Box-Storage.
    WHY: Keyframe-PNGs müssen extern erreichbar sein für GitHub Issue Comments.
    CONSEQUENCES: Gibt die öffentliche URL zurück, oder None bei Fehler.
    """
    box_url = os.environ.get("BOX_STORAGE_URL", "")
    box_key = os.environ.get("BOX_STORAGE_API_KEY", "")
    if not box_url or not box_key:
        return None

    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        # Multipart-Form wäre besser, aber urllib kann das nicht einfach.
        # Daher: Raw-Binary-Upload mit Filename im Header.
        filename = Path(file_path).name
        req = urllib.request.Request(
            f"{box_url.rstrip('/')}/api/v1/upload",
            data=file_bytes,
            method="POST",
            headers={
                "X-Box-Storage-Key": box_key,
                "Content-Type": "application/octet-stream",
                "X-Filename": filename,
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data.get("file", {}).get("url", "")
    except Exception:
        return None


def save_fail_report_to_disk(
    report_md: str,
    analysis: dict,
    output_dir: str | Path,
    run_id: str,
) -> Path:
    """
    Speichert den Fail-Report als Markdown + JSON auf Disk.
    WHY: Lokale Kopie als Backup falls GitHub/Box.com nicht erreichbar.
    CONSEQUENCES: Erstellt output_dir falls nicht vorhanden.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Markdown Report
    md_path = out / f"fail_report_{run_id}.md"
    md_path.write_text(report_md, encoding="utf-8")

    # Raw JSON
    json_path = out / f"fail_analysis_{run_id}.json"
    json_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")

    return md_path
