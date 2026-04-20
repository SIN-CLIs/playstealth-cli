# ================================================================================
# DATEI: test_circuit_breaker.py
# PROJEKT: A2A-SIN-Worker-heyPiggy (OpenSIN AI Agent System)
# ZWECK: 
# WICHTIG FÜR ENTWICKLER: 
#   - Ändere nichts ohne zu verstehen was passiert
#   - Jeder Kommentar erklärt WARUM etwas getan wird, nicht nur WAS
#   - Bei Fragen erst Code lesen, dann ändern
# ================================================================================

import unittest
from unittest.mock import patch


from circuit_breaker import CircuitBreaker, CircuitState


class CircuitBreakerTests(unittest.TestCase):
    # ========================================================================
    # KLASSE: CircuitBreakerTests(unittest.TestCase)
    # ZWECK: 
    # WICHTIG: 
    # METHODEN: 
    # ========================================================================
    
    def test_initial_state_is_closed_and_requests_are_allowed(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_initial_state_is_closed_and_requests_are_allowed
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        cb = CircuitBreaker()

        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertTrue(cb.is_closed)
        self.assertFalse(cb.is_open)
        self.assertTrue(cb.allow_request())

    def test_breaker_opens_after_failure_threshold(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_breaker_opens_after_failure_threshold
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)

        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.CLOSED)

        cb.record_failure()

        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertTrue(cb.is_open)
        self.assertEqual(cb.consecutive_failures, 3)
        self.assertEqual(cb.total_failures, 3)

    def test_open_breaker_rejects_until_recovery_timeout_expires(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_open_breaker_rejects_until_recovery_timeout_expires
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60)

        with patch("circuit_breaker.time.time", return_value=100.0):
            cb.record_failure()

        with patch("circuit_breaker.time.time", return_value=130.0):
            allowed = cb.allow_request()

        self.assertFalse(allowed)
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertEqual(cb.total_rejected, 1)

    def test_open_breaker_transitions_to_half_open_after_timeout(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_open_breaker_transitions_to_half_open_after_timeout
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60)

        with patch("circuit_breaker.time.time", return_value=100.0):
            cb.record_failure()

        with patch("circuit_breaker.time.time", return_value=160.0):
            allowed = cb.allow_request()

        self.assertTrue(allowed)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

    def test_success_in_half_open_closes_breaker_and_resets_failures(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_success_in_half_open_closes_breaker_and_resets_failures
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60)

        with patch("circuit_breaker.time.time", return_value=100.0):
            cb.record_failure()
        with patch("circuit_breaker.time.time", return_value=161.0):
            self.assertTrue(cb.allow_request())

        cb.record_success()

        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.consecutive_failures, 0)
        self.assertEqual(cb.total_successes, 1)

    def test_failure_in_half_open_reopens_breaker_immediately(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_failure_in_half_open_reopens_breaker_immediately
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=60)

        with patch("circuit_breaker.time.time", return_value=100.0):
            cb.record_failure()
            cb.record_failure()
        with patch("circuit_breaker.time.time", return_value=161.0):
            self.assertTrue(cb.allow_request())
        with patch("circuit_breaker.time.time", return_value=162.0):
            cb.record_failure()

        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertEqual(cb.total_failures, 3)

    def test_reset_clears_all_counters_and_restores_closed_state(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_reset_clears_all_counters_and_restores_closed_state
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60)

        with patch("circuit_breaker.time.time", return_value=100.0):
            cb.record_failure()
        with patch("circuit_breaker.time.time", return_value=130.0):
            _ = cb.allow_request()
        cb.record_success()
        cb.reset()

        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.consecutive_failures, 0)
        self.assertEqual(cb.last_failure_time, 0.0)
        self.assertEqual(cb.total_failures, 0)
        self.assertEqual(cb.total_successes, 0)
        self.assertEqual(cb.total_rejected, 0)

    def test_status_dict_exposes_structured_monitoring_snapshot(self):
    # -------------------------------------------------------------------------
    # FUNKTION: test_status_dict_exposes_structured_monitoring_snapshot
    # PARAMETER: self
    # ZWECK: 
    # WAS PASSIERT HIER: 
    # WARUM DIESER WEG: 
    # ACHTUNG: 
    # -------------------------------------------------------------------------
    
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=45)

        with patch("circuit_breaker.time.time", return_value=100.0):
            cb.record_failure()
        with patch("circuit_breaker.time.time", return_value=112.6):
            status = cb.status_dict()

        self.assertEqual(status["state"], "closed")
        self.assertEqual(status["consecutive_failures"], 1)
        self.assertEqual(status["total_failures"], 1)
        self.assertEqual(status["total_successes"], 0)
        self.assertEqual(status["total_rejected"], 0)
        self.assertEqual(status["failure_threshold"], 2)
        self.assertEqual(status["recovery_timeout"], 45)
        self.assertEqual(status["time_since_last_failure"], 12.6)
