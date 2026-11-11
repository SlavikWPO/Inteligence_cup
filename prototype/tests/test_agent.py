"""Тесты ключевых свойств из компонента 4 (без вызова модели)."""
import json
import os
import tempfile
import unittest
from datetime import date

from agent.analyst import analyze_and_deliver, validate_signal
from agent.llm import RuleBasedLLM
from agent.pipeline import has_pii, mask_pii, process

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "tickets.jsonl")
AS_OF = date(2026, 11, 10)

def run(tmp, n_min=5):
    enriched, dropped, stats = process(DATA, RuleBasedLLM())
    report, state = analyze_and_deliver(enriched, AS_OF, os.path.join(tmp, "state.json"), n_min=n_min)
    return enriched, dropped, stats, report, state

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_pii_masked(self):
        s = mask_pii("Иванов Пётр Сергеевич, тел. +7 912 345-67-89, ИНН 7701234567, a.b@mail.ru, Петров А.В.")
        self.assertFalse(has_pii(s))
        for token in ("[ФИО]", "[ТЕЛЕФОН]", "[ИНН]", "[EMAIL]"):
            self.assertIn(token, s)

    def test_no_pii_reaches_model(self):
        enriched, *_ = run(self.tmp)
        self.assertFalse(any(has_pii(e["text"]) for e in enriched))

    def test_junk_filtered_before_llm(self):
        _, dropped, *_ = run(self.tmp)
        self.assertEqual({r for _, r in dropped}, {"пустой", "служебный", "повторный тикет"})

    def test_asr_vl_only_when_needed(self):
        _, _, stats, *_ = run(self.tmp)
        self.assertEqual((stats["asr_calls"], stats["vl_calls"]), (1, 1))

    def test_threshold_counts_distinct_clients(self):
        *_, report, _ = run(self.tmp)
        below = dict(report["below_threshold"])
        self.assertEqual(below["vehicle_catalog"], 1)   # 5 тикетов, но один клиент — не сигнал
        self.assertIn("edit_contacts", report["created"])  # 6 тикетов, 5 разных клиентов — сигнал

    def test_growth_rule(self):
        *_, report, state = run(self.tmp)
        self.assertIn("queue_booking", report["created"])
        self.assertLess(state["queue_booking"]["signal"]["clients"], 5)

    def test_prompt_injection_not_signal(self):
        *_, report, state = run(self.tmp)
        self.assertEqual(report["suspicious"], ["T-1021"])
        self.assertTrue(all("T-1021" not in s["signal"]["evidence"] for s in state.values()))

    def test_idempotent_rerun(self):
        run(self.tmp)
        *_, report, state = run(self.tmp)
        self.assertEqual(report["created"], [])
        self.assertEqual(sorted(report["unchanged"]), ["edit_contacts", "queue_booking"])
        self.assertEqual(len(state), 2)

    def test_signal_schema_and_json(self):
        *_, state = run(self.tmp)
        for s in state.values():
            self.assertTrue(validate_signal(s["signal"]))
            json.loads(json.dumps(s["signal"], ensure_ascii=False))

    def test_validator_blocks_pii(self):
        *_, state = run(self.tmp)
        sig = dict(state["edit_contacts"]["signal"]); sig["examples"] = ["Звоните +7 912 345-67-89"]
        with self.assertRaises(ValueError):
            validate_signal(sig)

if __name__ == "__main__":
    unittest.main()
