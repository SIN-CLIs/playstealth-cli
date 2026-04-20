# ================================================================================
# DATEI: test_observability.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import cast
from unittest.mock import patch


from observability import RunSummary


class RunSummaryStepTests(unittest.TestCase):
    # ========================================================================
    # KLASSE: RunSummaryStepTests(unittest.TestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_record_step_updates_step_counters_consistently(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_record_step_updates_step_counters_consistently
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        summary = RunSummary(run_id="run-1", start_time=10.0)

        with patch("observability.time.time", side_effect=[11.0, 12.0, 13.0]):
            summary.record_step(1, "PROCEED", "dashboard", action="click", duration=0.4)
            summary.record_step(2, "RETRY", "survey", action="retry", duration=1.1)
            summary.record_step(
                3,
                "STOP",
                "error",
                action="none",
                duration=0.2,
                success=True,
                error="vision blocked",
            )

        self.assertEqual(summary.total_steps, 3)
        self.assertEqual(summary.successful_steps, 1)
        self.assertEqual(summary.retry_steps, 1)
        self.assertEqual(summary.failed_steps, 1)
        self.assertEqual(len(summary.step_metrics), 3)
        self.assertEqual(summary.step_metrics[-1].error, "vision blocked")

    def test_record_step_counts_explicit_unsuccessful_step_even_without_stop_verdict(
        self,
    ):
    # -------------------------------------------------------------------------
    # FUNKTION: test_record_step_counts_explicit_unsuccessful_step_even_without_stop_verdict
    # PARAMETER: 
        self,
    
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        summary = RunSummary(run_id="run-2", start_time=10.0)

        with patch("observability.time.time", return_value=11.0):
            summary.record_step(
                1,
                "PROCEED",
                "dashboard",
                action="click",
                duration=0.9,
                success=False,
                error="bridge timeout",
            )

        self.assertEqual(summary.total_steps, 1)
        self.assertEqual(summary.successful_steps, 1)
        self.assertEqual(summary.failed_steps, 1)
        self.assertFalse(summary.step_metrics[0].success)


class RunSummaryAggregationTests(unittest.TestCase):
    # ========================================================================
    # KLASSE: RunSummaryAggregationTests(unittest.TestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_duration_success_rate_and_average_timings_are_computed(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_duration_success_rate_and_average_timings_are_computed
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        summary = RunSummary(run_id="run-3", start_time=100.0)

        with patch("observability.time.time", side_effect=[101.0, 102.0]):
            summary.record_step(1, "PROCEED", "dashboard", duration=1.0)
            summary.record_step(2, "RETRY", "survey", duration=2.0)

        summary.record_vision_call(1.2)
        summary.record_vision_call(1.8)
        summary.record_bridge_call(0.5)
        summary.record_bridge_call(1.0)
        summary.record_survey_completed()

        with patch("observability.time.time", return_value=130.5):
            summary.finalize(exit_reason="completed", page_state="dashboard")

        self.assertEqual(summary.duration_seconds, 30.5)
        self.assertEqual(summary.success_rate, 0.5)
        self.assertEqual(summary.avg_vision_time, 1.5)
        self.assertEqual(summary.avg_bridge_time, 0.75)
        self.assertEqual(summary.surveys_completed, 1)
        self.assertEqual(summary.exit_reason, "completed")
        self.assertEqual(summary.final_page_state, "dashboard")

    def test_zero_step_summary_reports_zero_rates(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_zero_step_summary_reports_zero_rates
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        summary = RunSummary(run_id="run-empty", start_time=200.0)

        self.assertEqual(summary.success_rate, 0.0)
        self.assertEqual(summary.avg_vision_time, 0.0)
        self.assertEqual(summary.avg_bridge_time, 0.0)


class RunSummarySerializationTests(unittest.TestCase):
    # ========================================================================
    # KLASSE: RunSummarySerializationTests(unittest.TestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_to_dict_without_steps_excludes_step_payload(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_to_dict_without_steps_excludes_step_payload
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        summary = RunSummary(run_id="run-4", start_time=50.0)

        with patch("observability.time.time", return_value=51.0):
            summary.record_step(1, "PROCEED", "dashboard", action="click", duration=0.2)
        with patch("observability.time.time", return_value=53.0):
            summary.finalize(exit_reason="done", page_state="dashboard")

        data = summary.to_dict(include_steps=False)

        self.assertEqual(data["run_id"], "run-4")
        self.assertNotIn("steps", data)
        self.assertEqual(data["total_steps"], 1)

    def test_to_dict_with_steps_serializes_step_metrics(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_to_dict_with_steps_serializes_step_metrics
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        summary = RunSummary(run_id="run-5", start_time=50.0)

        with patch("observability.time.time", return_value=51.0):
            summary.record_step(
                1,
                "RETRY",
                "survey",
                action="retry",
                duration=0.4567,
                success=False,
                error="transient vision error",
            )
        with patch("observability.time.time", return_value=55.0):
            summary.finalize(exit_reason="stopped", page_state="error")

        data = summary.to_dict(include_steps=True)
        steps = cast(list[dict[str, object]], data["steps"])

        self.assertIn("steps", data)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["step"], 1)
        self.assertEqual(steps[0]["verdict"], "RETRY")
        self.assertEqual(steps[0]["error"], "transient vision error")
        self.assertEqual(steps[0]["duration"], 0.457)

    def test_save_to_file_writes_json_payload_to_disk(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_save_to_file_writes_json_payload_to_disk
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        summary = RunSummary(run_id="run-6", start_time=10.0)

        with patch("observability.time.time", return_value=11.0):
            summary.record_step(1, "PROCEED", "dashboard", action="click", duration=0.1)
        with patch("observability.time.time", return_value=15.0):
            summary.finalize(exit_reason="done", page_state="dashboard")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "nested" / "run_summary.json"

            summary.save_to_file(output_path, include_steps=True)

            self.assertTrue(output_path.is_file())
            payload = cast(
                dict[str, object], json.loads(output_path.read_text(encoding="utf-8"))
            )
            saved_steps = cast(list[dict[str, object]], payload["steps"])
            self.assertEqual(payload["run_id"], "run-6")
            self.assertEqual(payload["exit_reason"], "done")
            self.assertEqual(len(saved_steps), 1)

    def test_print_summary_emits_human_readable_console_output(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_print_summary_emits_human_readable_console_output
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        summary = RunSummary(run_id="run-7", start_time=0.0)
        summary.total_steps = 4
        summary.successful_steps = 2
        summary.retry_steps = 1
        summary.failed_steps = 1
        summary.total_vision_calls = 2
        summary.total_vision_time_seconds = 3.0
        summary.total_bridge_calls = 1
        summary.total_bridge_time_seconds = 0.5
        summary.surveys_completed = 1
        summary.captcha_encounters = 2
        summary.loop_detections = 1
        summary.end_time = 125.0
        summary.exit_reason = "done"
        summary.final_page_state = "dashboard"

        out = io.StringIO()
        with redirect_stdout(out):
            summary.print_summary()

        text = out.getvalue()
        self.assertIn("RUN SUMMARY — run-7", text)
        self.assertIn("Schritte: 4", text)
        self.assertIn("Erfolgsrate: 50%", text)
        self.assertIn("Surveys abgeschlossen: 1", text)
        self.assertIn("⚠️ Captchas: 2", text)
        self.assertIn("Exit: done | State: dashboard", text)
