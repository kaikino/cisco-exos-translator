# Optional AI migration summary.
#
# This module is the only place that talks to an LLM, and it is strictly
# explanatory: it reads the findings document the deterministic pipeline
# already produced and writes Markdown prose. It never produces, edits or
# validates EXOS configuration, and nothing else in the translator imports it
# unless the user asks for a report.
#
# The Anthropic SDK is an optional dependency, imported inside the client so
# the translator keeps working (and keeps its stdlib-only footprint) when the
# SDK is not installed.

from __future__ import annotations

import json
import os
from typing import Optional

# Default model; override with EXOS_TRANSLATOR_AI_MODEL or --ai-model.
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 8000
DEFAULT_EFFORT = "medium"

# Environment variables read (never logged, never written to any artifact):
#   ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN -- resolved by the SDK itself
#   EXOS_TRANSLATOR_AI_MODEL                 -- model id override
ENV_MODEL = "EXOS_TRANSLATOR_AI_MODEL"


class AIConfigurationError(RuntimeError):
    # The AI feature is misconfigured (missing SDK, missing credentials).
    pass


class AIRequestError(RuntimeError):
    # The provider was reachable but the request did not produce a summary.
    pass


SYSTEM_PROMPT = """\
You are documenting the result of a deterministic Cisco IOS to Extreme EXOS \
configuration translation. A tool has already performed the translation; your \
only job is to explain its findings to a network engineer.

Rules you must follow:
- Base every statement solely on the findings JSON supplied in the user message.
- Never write, invent, correct or suggest EXOS configuration syntax, and never \
imply that your output is deployable configuration.
- Do not claim an unsupported feature has an EXOS equivalent unless the finding \
itself supplies that guidance in its "action" field; otherwise say the \
replacement must be determined by the engineer.
- If the findings do not cover something, say the information is not available \
rather than guessing.
- Keep facts, assumptions and recommendations clearly separated, and attribute \
counts to the summary block.

Write GitHub-flavoured Markdown with exactly these level-2 sections, in order:
1. Executive summary
2. Successfully translated areas
3. Unsupported items
4. Partially translated items
5. Assumptions made
6. Manual review requirements
7. Recommended validation steps

If a section has no corresponding findings, keep the heading and say so in one \
line."""


class LLMClient:
    # Provider-neutral interface. Implementations take a system prompt and a
    # user prompt and return the model's text.
    def complete(self, system: str, prompt: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def describe(self) -> dict:
        # Non-secret metadata about the client, recorded in the findings file.
        return {"provider": "unknown"}


class AnthropicLLMClient(LLMClient):
    # Anthropic Messages API via the official SDK. Credentials are resolved by
    # the SDK from the environment; this code never reads or stores their value.
    def __init__(
        self,
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str = DEFAULT_EFFORT,
    ) -> None:
        self.model = model or os.environ.get(ENV_MODEL) or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self.effort = effort

    def describe(self) -> dict:
        return {"provider": "anthropic", "model": self.model}

    def complete(self, system: str, prompt: str) -> str:
        try:
            import anthropic
        except ImportError as exc:
            raise AIConfigurationError(
                "the optional 'anthropic' package is not installed; "
                "run 'pip install anthropic' or drop --ai-summary"
            ) from exc

        try:
            client = anthropic.Anthropic()
        except Exception as exc:  # missing/invalid credentials surface here
            raise AIConfigurationError(f"could not initialise the Anthropic client: {exc}") from exc

        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                output_config={"effort": self.effort},
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError as exc:
            raise AIConfigurationError(
                "the Anthropic API rejected the credentials; set ANTHROPIC_API_KEY "
                "(the translator never reads or stores its value)"
            ) from exc
        except anthropic.NotFoundError as exc:
            raise AIConfigurationError(
                f"model '{self.model}' is not available to these credentials; "
                f"set {ENV_MODEL} or --ai-model"
            ) from exc
        except Exception as exc:
            raise AIRequestError(f"the AI request failed: {exc}") from exc

        if getattr(response, "stop_reason", None) == "refusal":
            raise AIRequestError("the model declined to summarise this migration")

        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        if not text.strip():
            raise AIRequestError("the model returned an empty summary")
        return text


class MigrationSummaryGenerator:
    # Turns a findings document into a Markdown migration report.
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    # What leaves the machine, in one place, so it can be described honestly to
    # the user and asserted on in tests: the structured findings only -- never
    # the Cisco config file and never the generated EXOS config. Note that
    # findings do quote the individual Cisco commands that could not be
    # translated; see disclosure() and the README's privacy section.
    def payload(self, findings_document: dict) -> dict:
        document = dict(findings_document)
        document.pop("generation", None)
        return document

    def build_prompt(self, findings_document: dict) -> str:
        payload = json.dumps(self.payload(findings_document), indent=2)
        return (
            "Here is the migration findings document produced by the translator. "
            "Summarise it for the engineer who has to deploy and verify the "
            "converted switch. Use only what is in this JSON.\n\n"
            "```json\n" + payload + "\n```\n"
        )

    def generate_summary(self, findings_document: dict) -> str:
        return self.client.complete(SYSTEM_PROMPT, self.build_prompt(findings_document))


# One line describing exactly what an --ai-summary run transmits, shown to the
# user before the request so the disclosure is not buried in documentation.
def disclosure(findings_document: dict, client: LLMClient) -> str:
    info = client.describe()
    count = len(findings_document.get("findings", []))
    return (
        f"AI summary: sending {count} structured finding(s) for "
        f"'{findings_document['input']['filename']}' to {info.get('provider')} "
        f"model '{info.get('model')}'. The Cisco config file and the generated "
        f"EXOS config are NOT sent, but findings quote the untranslated Cisco "
        f"commands and can contain hostnames, interface descriptions, VLAN "
        f"names, IP addresses and ACL contents."
    )
