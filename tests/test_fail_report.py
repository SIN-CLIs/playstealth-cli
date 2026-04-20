#!/usr/bin/env python3
# ================================================================================
# DATEI: test_fail_report.py
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
Tests für fail_report.py — Fail Report Generator + Publisher
================================================================================
WHY: Fail-Reports sind das öffentliche Gesicht jedes Worker-Versagens.
     Falsche Formatierung, fehlende Felder oder kaputte GitHub-Posts
     verhindern effektives Debugging.
CONSEQUENCES: Tests stellen sicher dass Reports immer korrekt formatiert sind
     und alle Edge-Cases (leere Analyse, fehlende Felder) abgedeckt sind.
================================================================================
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fail_report import (
    generate_fail_report_markdown,
    post_github_issue_comment,
    save_fail_report_to_disk,
    upload_to_box,
)


# ============================================================================
# HILFSFUNKTIONEN
# ============================================================================


def _sample_analysis() -> dict:
    """Standard-Analyse-Dict wie von NVIDIA zurückgegeben."""
    return {
        "root_cause": "Weiter-Button war nicht sichtbar",
        "affected_step": "Klick auf Weiter-Button Seite 3",
        "fix_recommendation": "Scroll-Down vor Klick-Versuch",
        "confidence_score": 0.85,
        "frame_evidence": "Frame 8 zeigt Button unter dem Fold",
        "captcha_detected": False,
        "timing_issue": True,
        "selector_issue": False,
        "loop_detected": False,
        "error": "",
    }


# ============================================================================
# UNIT TESTS — generate_fail_report_markdown
# ============================================================================


class TestGenerateFailReportMarkdown:
    # ========================================================================
    # KLASSE: TestGenerateFailReportMarkdown
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """Tests für die Markdown-Report-Generierung."""

    def test_basic_report(self):
        """WHY: Report muss alle relevanten Felder enthalten."""
        report = generate_fail_report_markdown(
            analysis=_sample_analysis(),
            run_id="test_run_001",
            total_steps=42,
            last_page_state="survey_active",
        )
        assert "test_run_001" in report
        assert "42" in report
        assert "survey_active" in report
        assert "Weiter-Button war nicht sichtbar" in report
        assert "Scroll-Down vor Klick-Versuch" in report

    def test_report_has_markdown_structure(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_report_has_markdown_structure
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Report muss valides Markdown sein (Headers, Tabelle)."""
        report = generate_fail_report_markdown(
            analysis=_sample_analysis(),
            run_id="run_md",
        )
        assert "## 🔴 HeyPiggy Worker Fail Report" in report
        assert "### Root Cause" in report
        assert "### NVIDIA Video-Vision Analyse" in report
        assert "| Feld | Wert |" in report

    def test_report_with_keyframe_urls(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_report_with_keyframe_urls
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Keyframe-URLs müssen als Link-Liste erscheinen."""
        report = generate_fail_report_markdown(
            analysis=_sample_analysis(),
            run_id="run_urls",
            keyframe_urls=[
                "https://box.com/frame1.png",
                "https://box.com/frame2.png",
            ],
        )
        assert "### Keyframes" in report
        assert "https://box.com/frame1.png" in report
        assert "Frame 1:" in report
        assert "Frame 2:" in report

    def test_report_with_error(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_report_with_error
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Analyse-Fehler müssen im Report sichtbar sein."""
        analysis = _sample_analysis()
        analysis["error"] = "NVIDIA Timeout nach 120s"
        report = generate_fail_report_markdown(analysis=analysis, run_id="err_run")
        assert "### ⚠️ Analyse-Fehler" in report
        assert "NVIDIA Timeout" in report

    def test_report_without_error(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_report_without_error
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Ohne Fehler soll keine Fehler-Sektion erscheinen."""
        analysis = _sample_analysis()
        analysis["error"] = ""
        report = generate_fail_report_markdown(analysis=analysis, run_id="ok_run")
        assert "### ⚠️ Analyse-Fehler" not in report

    def test_report_with_empty_analysis(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_report_with_empty_analysis
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Leere Analyse darf nicht crashen — Defaults müssen greifen."""
        report = generate_fail_report_markdown(
            analysis={},
            run_id="empty_run",
        )
        assert "Unbekannt" in report  # Default root_cause
        assert "N/A" in report  # Default affected_step

    def test_report_json_block(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_report_json_block
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Raw-Analysis muss als JSON-Block im Report sein."""
        report = generate_fail_report_markdown(
            analysis=_sample_analysis(),
            run_id="json_run",
        )
        assert "### Raw Analysis" in report
        assert "```json" in report

    def test_confidence_formatting_float(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_confidence_formatting_float
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Float-Confidence muss als Prozent formatiert werden (z.B. 85%)."""
        analysis = _sample_analysis()
        analysis["confidence_score"] = 0.85
        report = generate_fail_report_markdown(analysis=analysis, run_id="conf")
        assert "85%" in report

    def test_confidence_formatting_int(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_confidence_formatting_int
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Integer-Confidence muss auch korrekt formatiert werden."""
        analysis = _sample_analysis()
        analysis["confidence_score"] = 1
        report = generate_fail_report_markdown(analysis=analysis, run_id="conf_int")
        # 1 als int → 100% via :.0%
        assert "100%" in report

    def test_boolean_flags_display(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_boolean_flags_display
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Boolean-Flags müssen visuell erkennbar sein (✅/❌)."""
        analysis = _sample_analysis()
        analysis["captcha_detected"] = True
        analysis["timing_issue"] = False
        report = generate_fail_report_markdown(analysis=analysis, run_id="flags")
        assert "✅ JA" in report  # captcha_detected = True
        assert "❌ Nein" in report  # timing_issue = False

    def test_max_keyframe_urls(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_max_keyframe_urls
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Maximal 6 Keyframe-URLs werden angezeigt (übersichtlich)."""
        urls = [f"https://box.com/frame{i}.png" for i in range(10)]
        report = generate_fail_report_markdown(
            analysis=_sample_analysis(),
            run_id="max_urls",
            keyframe_urls=urls,
        )
        assert "Frame 6:" in report
        assert "Frame 7:" not in report  # Abgeschnitten bei 6


# ============================================================================
# UNIT TESTS — post_github_issue_comment
# ============================================================================


class TestPostGithubIssueComment:
    # ========================================================================
    # KLASSE: TestPostGithubIssueComment
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """Tests für die GitHub Issue Comment Funktion."""

    @patch("fail_report.subprocess.run")
    def test_successful_post(self, mock_run):
        """WHY: Erfolgreicher gh-Aufruf muss True zurückgeben."""
        mock_run.return_value = MagicMock(returncode=0)
        result = post_github_issue_comment(
            repo="OpenSIN-AI/A2A-SIN-Worker-heypiggy",
            issue_number=37,
            comment_body="Test Comment",
        )
        assert result is True
        mock_run.assert_called_once()

    @patch("fail_report.subprocess.run")
    def test_failed_post(self, mock_run):
    # -------------------------------------------------------------------------
    # FUNKTION: test_failed_post
    # PARAMETER: self, mock_run
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Fehlgeschlagener gh-Aufruf muss False zurückgeben, kein Crash."""
        mock_run.return_value = MagicMock(returncode=1)
        result = post_github_issue_comment(
            repo="test/repo",
            issue_number=1,
            comment_body="Test",
        )
        assert result is False

    @patch("fail_report.subprocess.run")
    def test_exception_returns_false(self, mock_run):
    # -------------------------------------------------------------------------
    # FUNKTION: test_exception_returns_false
    # PARAMETER: self, mock_run
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Exceptions (gh nicht installiert, Timeout) dürfen nicht crashen."""
        mock_run.side_effect = FileNotFoundError("gh not found")
        result = post_github_issue_comment(
            repo="test/repo",
            issue_number=1,
            comment_body="Test",
        )
        assert result is False

    @patch("fail_report.subprocess.run")
    def test_body_truncated_at_65k(self, mock_run):
    # -------------------------------------------------------------------------
    # FUNKTION: test_body_truncated_at_65k
    # PARAMETER: self, mock_run
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: GitHub hat ein 65535 Zeichen Limit für Comments."""
        mock_run.return_value = MagicMock(returncode=0)
        long_body = "x" * 100_000
        post_github_issue_comment("test/repo", 1, long_body)
        # Prüfe dass der an gh übergebene Body abgeschnitten wurde
        call_args = mock_run.call_args[0][0]
        body_arg = call_args[call_args.index("--body") + 1]
        assert len(body_arg) <= 65_000


# ============================================================================
# UNIT TESTS — upload_to_box
# ============================================================================


class TestUploadToBox:
    # ========================================================================
    # KLASSE: TestUploadToBox
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """Tests für den Box.com Upload."""

    def test_no_env_vars_returns_none(self):
        """WHY: Ohne BOX_STORAGE_URL/KEY muss None zurückkommen (kein Crash)."""
        with patch.dict("os.environ", {}, clear=True):
            result = upload_to_box("/tmp/test.png")
        assert result is None

    @patch("fail_report.urllib.request.urlopen")
    def test_successful_upload(self, mock_urlopen):
        """WHY: Erfolgreicher Upload muss die URL zurückgeben."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"file": {"url": "https://box.com/public/test.png"}}
        ).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        with patch.dict(
            "os.environ",
            {
                "BOX_STORAGE_URL": "http://box-service:3000",
                "BOX_STORAGE_API_KEY": "test-key",
            },
        ):
            with patch("builtins.open", MagicMock()):
                # Mock open() damit keine echte Datei gelesen wird
                import builtins

                original_open = builtins.open

                def mock_open(path, *args, **kwargs):
    # -------------------------------------------------------------------------
    # FUNKTION: mock_open
    # PARAMETER: path, *args, **kwargs
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
                    m = MagicMock()
                    m.__enter__ = lambda s: MagicMock(read=lambda: b"fake_png_data")
                    m.__exit__ = MagicMock(return_value=False)
                    return m

                with patch("builtins.open", mock_open):
                    result = upload_to_box("/tmp/test.png")

        assert result == "https://box.com/public/test.png"

    def test_upload_exception_returns_none(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_upload_exception_returns_none
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Netzwerk-Fehler beim Upload dürfen nicht crashen."""
        with patch.dict(
            "os.environ",
            {
                "BOX_STORAGE_URL": "http://box-service:3000",
                "BOX_STORAGE_API_KEY": "test-key",
            },
        ):
            with patch("builtins.open", side_effect=FileNotFoundError):
                result = upload_to_box("/nonexistent/file.png")
        assert result is None


# ============================================================================
# UNIT TESTS — save_fail_report_to_disk
# ============================================================================


class TestSaveFailReportToDisk:
    # ========================================================================
    # KLASSE: TestSaveFailReportToDisk
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    """Tests für die lokale Speicherung von Fail-Reports."""

    def test_saves_markdown_and_json(self, tmp_path: Path):
        """WHY: Beide Dateien (MD + JSON) müssen geschrieben werden."""
        analysis = _sample_analysis()
        report_md = "## Test Report\nContent here."
        md_path = save_fail_report_to_disk(
            report_md=report_md,
            analysis=analysis,
            output_dir=tmp_path,
            run_id="test_001",
        )
        assert md_path.exists()
        assert md_path.name == "fail_report_test_001.md"
        assert md_path.read_text() == report_md

        json_path = tmp_path / "fail_analysis_test_001.json"
        assert json_path.exists()
        loaded = json.loads(json_path.read_text())
        assert loaded["root_cause"] == analysis["root_cause"]

    def test_creates_nested_directory(self, tmp_path: Path):
    # -------------------------------------------------------------------------
    # FUNKTION: test_creates_nested_directory
    # PARAMETER: self, tmp_path: Path
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Nicht existierende Verzeichnisse müssen automatisch erstellt werden."""
        deep = tmp_path / "a" / "b" / "c"
        save_fail_report_to_disk(
            report_md="test",
            analysis={},
            output_dir=deep,
            run_id="nested",
        )
        assert deep.exists()
        assert (deep / "fail_report_nested.md").exists()

    def test_returns_md_path(self, tmp_path: Path):
    # -------------------------------------------------------------------------
    # FUNKTION: test_returns_md_path
    # PARAMETER: self, tmp_path: Path
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        """WHY: Rückgabewert muss der Pfad zur Markdown-Datei sein."""
        result = save_fail_report_to_disk(
            report_md="test",
            analysis={},
            output_dir=tmp_path,
            run_id="ret",
        )
        assert isinstance(result, Path)
        assert result.suffix == ".md"
