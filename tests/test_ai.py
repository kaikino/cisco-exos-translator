# AI summary component. No test here makes a network call: every test either
# uses a fake LLMClient or asserts on prompt/payload construction.

from __future__ import annotations

import json
import unittest

from cisco_exos_translator import ai

DOCUMENT = {
    "schema_version": "1.0",
    "input": {"filename": "sw1.cfg", "hostname": "SW1"},
    "summary": {"translated": 3, "unsupported": 1, "warnings": 1, "errors": 0},
    "findings": [
        {"id": "unsupported.qos#1", "status": "unsupported", "feature": "qos",
         "message": "'mls qos' has no EXOS output"},
    ],
    "artifacts": {"exos_config": "sw1.xsf"},
    "generation": {"status": "completed", "ai_report": {"requested": True}},
}


class FakeClient(ai.LLMClient):
    def __init__(self, reply="## Executive summary\nAll good.", error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def complete(self, system, prompt):
        self.calls.append((system, prompt))
        if self.error:
            raise self.error
        return self.reply

    def describe(self):
        return {"provider": "fake", "model": "fake-1"}


class SummaryGeneratorTest(unittest.TestCase):
    def test_generates_markdown_from_findings(self):
        client = FakeClient()
        summary = ai.MigrationSummaryGenerator(client).generate_summary(DOCUMENT)
        self.assertIn("Executive summary", summary)
        system, prompt = client.calls[0]
        for section in ("Executive summary", "Unsupported items", "Assumptions made",
                        "Manual review requirements", "Recommended validation steps"):
            self.assertIn(section, system)
        self.assertIn("only what is in this JSON", prompt)
        self.assertIn("unsupported.qos#1", prompt)

    def test_system_prompt_forbids_generating_configuration(self):
        system = ai.SYSTEM_PROMPT
        self.assertIn("Never write, invent, correct or suggest EXOS configuration", system)
        self.assertIn("Base every statement solely on the findings JSON", system)
        self.assertIn("unless the finding", system)

    def test_payload_is_findings_only(self):
        payload = ai.MigrationSummaryGenerator(FakeClient()).payload(DOCUMENT)
        # Exactly the findings document minus its generation block: nothing is
        # added, so the raw Cisco config and the generated .xsf are never sent.
        # (Findings do quote untranslated Cisco commands -- see disclosure().)
        self.assertEqual(
            payload, {k: v for k, v in DOCUMENT.items() if k != "generation"}
        )
        self.assertNotIn("generation", payload)
        self.assertIsNot(payload, DOCUMENT)

    def test_failure_propagates_as_ai_error(self):
        client = FakeClient(error=ai.AIRequestError("boom"))
        with self.assertRaises(ai.AIRequestError):
            ai.MigrationSummaryGenerator(client).generate_summary(DOCUMENT)

    def test_disclosure_names_what_is_sent(self):
        message = ai.disclosure(DOCUMENT, FakeClient())
        self.assertIn("1 structured finding(s)", message)
        self.assertIn("sw1.cfg", message)
        self.assertIn("are NOT sent", message)
        self.assertIn("quote the untranslated Cisco commands", message)
        self.assertIn("interface descriptions", message)


class AnthropicClientTest(unittest.TestCase):
    def test_model_defaults_and_env_override(self):
        self.assertEqual(ai.AnthropicLLMClient().model, ai.DEFAULT_MODEL)
        self.assertEqual(ai.AnthropicLLMClient(model="custom-1").model, "custom-1")

    def test_describe_has_no_credentials(self):
        described = ai.AnthropicLLMClient().describe()
        self.assertEqual(set(described), {"provider", "model"})

    def test_missing_sdk_is_a_configuration_error(self):
        # Simulate the optional dependency being absent.
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = fake_import
        try:
            with self.assertRaises(ai.AIConfigurationError) as ctx:
                ai.AnthropicLLMClient().complete("s", "p")
        finally:
            builtins.__import__ = real_import
        self.assertIn("pip install anthropic", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
