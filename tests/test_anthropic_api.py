"""Offline regression tests for Claude response parsing and cost accounting."""

import io
import json
import unittest
from unittest.mock import patch

from nodalia_guardian.anthropic_api import AnthropicAPIError, analyze


class FakeLedger:
    def __init__(self):
        self.reservations = []
        self.settlements = []

    def reserve(self, model, payload_chars, max_output_tokens):
        self.reservations.append((model, payload_chars, max_output_tokens))
        return (17, 99_000)

    def settle(self, request_id, model, input_tokens, output_tokens):
        self.settlements.append((request_id, model, input_tokens, output_tokens))
        return 2_000


class ClaudeResponseTests(unittest.TestCase):
    @staticmethod
    def message(*, stop_reason="end_turn", blocks=None, usage=None):
        return json.dumps({
            "type": "message",
            "stop_reason": stop_reason,
            "content": blocks if blocks is not None else [
                {"type": "text", "text": '{"verdict":"comment"}'}
            ],
            "usage": usage if usage is not None else {
                "input_tokens": 120, "output_tokens": 30
            },
        }).encode("utf-8")

    def test_sonnet_uses_between_tools_and_extracts_text(self):
        ledger = FakeLedger()
        with patch("nodalia_guardian.anthropic_api.urllib.request.urlopen",
                   return_value=io.BytesIO(self.message())) as urlopen:
            output, cost = analyze({"repo": "getnodalia/agora"}, "test-key",
                                   "claude-sonnet-5-5", 3000, ledger)
            body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(body["thinking"], {"type": "between_tools"})
        self.assertEqual(body["max_tokens"], 3000)
        self.assertEqual(output, '{"verdict":"comment"}')
        self.assertEqual(cost, 0.002)
        self.assertEqual(ledger.settlements, [(17, "claude-sonnet-5-5", 120, 30)])

    def test_opus_does_not_use_sonnet_only_thinking_mode(self):
        ledger = FakeLedger()
        with patch("nodalia_guardian.anthropic_api.urllib.request.urlopen",
                   return_value=io.BytesIO(self.message())) as urlopen:
            analyze({}, "test-key", "claude-opus-5-5", 3000, ledger)
            body = json.loads(urlopen.call_args.args[0].data)
        self.assertNotIn("thinking", body)

    def test_thinking_only_max_tokens_is_explained_and_counted(self):
        ledger = FakeLedger()
        response = self.message(stop_reason="max_tokens",
                                blocks=[{"type": "thinking", "thinking": "", "signature": "sig"}])
        with patch("nodalia_guardian.anthropic_api.urllib.request.urlopen",
                   return_value=io.BytesIO(response)):
            with self.assertRaisesRegex(AnthropicAPIError, "max_tokens"):
                analyze({}, "test-key", "claude-sonnet-5-5", 3000, ledger)
        self.assertEqual(len(ledger.settlements), 1)

    def test_empty_text_is_explained_and_counted(self):
        ledger = FakeLedger()
        with patch("nodalia_guardian.anthropic_api.urllib.request.urlopen",
                   return_value=io.BytesIO(self.message(blocks=[]))):
            with self.assertRaisesRegex(AnthropicAPIError, "no text content"):
                analyze({}, "test-key", "claude-sonnet-5-5", 3000, ledger)
        self.assertEqual(len(ledger.settlements), 1)

    def test_invalid_usage_preserves_reservation(self):
        ledger = FakeLedger()
        response = json.dumps({"content": [{"type": "text", "text": "hello"}],
                               "stop_reason": "end_turn"}).encode("utf-8")
        with patch("nodalia_guardian.anthropic_api.urllib.request.urlopen",
                   return_value=io.BytesIO(response)):
            with self.assertRaisesRegex(AnthropicAPIError, "invalid usage"):
                analyze({}, "test-key", "claude-sonnet-5-5", 3000, ledger)
        self.assertEqual(len(ledger.reservations), 1)
        self.assertEqual(ledger.settlements, [])


if __name__ == "__main__":
    unittest.main()
