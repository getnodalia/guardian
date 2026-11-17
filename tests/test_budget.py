import tempfile
import unittest
from pathlib import Path

from nodalia_guardian.budget import BudgetExceeded, BudgetLedger, price_micro_usd


class BudgetTests(unittest.TestCase):
    def test_current_sonnet_base_price(self):
        self.assertEqual(price_micro_usd("claude-sonnet-5-5", 1_000_000, 1_000_000), 12_000_000)

    def test_budget_reservation_and_settlement(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = BudgetLedger(Path(td) / "ledger.sqlite", 0.10)
            request_id, reserved = ledger.reserve("claude-sonnet-5-5", 5000, 1000)
            self.assertGreater(reserved, 0)
            self.assertEqual(ledger.total_micro(), reserved)
            actual = ledger.settle(request_id, "claude-sonnet-5-5", 1000, 200)
            self.assertEqual(actual, 4000)
            self.assertEqual(ledger.total_micro(), 4000)
            with self.assertRaises(ValueError):
                ledger.settle(request_id, "claude-sonnet-5-5", 1, 1)

    def test_budget_blocks_before_request(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = BudgetLedger(Path(td) / "ledger.sqlite", 0.001)
            with self.assertRaises(BudgetExceeded):
                ledger.reserve("claude-sonnet-5-5", 5000, 4096)
            self.assertEqual(ledger.total_micro(), 0)

    def test_model_allowlist(self):
        with self.assertRaises(ValueError):
            price_micro_usd("unknown-model", 100, 100)
