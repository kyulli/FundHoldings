"""Pluggable LLM backends for template generation.

The backend is deliberately a thin, swappable seam. Nothing else in this
package imports an SDK directly, so the project can change model vendors — or
run with no vendor at all — without touching the generator, the validator, or
the deterministic replay path.

Backends available:

  anthropic  -- calls the Claude API. Needs ANTHROPIC_API_KEY in the
                environment and the `anthropic` package installed.
  echo       -- returns a canned response supplied by the caller. Used by tests
                and by the offline demo so the whole flow can be exercised with
                no API key and no network.
  manual     -- writes the prompt to a file and reads a human-pasted response
                back. Useful when the API is unavailable, or when someone wants
                to review exactly what would be sent before sending it.

Every backend returns raw text. Parsing and validation happen in propose.py,
so a malformed or hallucinated response fails the same way regardless of which
backend produced it.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 8000


class LLMBackend(ABC):
    """Minimal interface: text in, text out."""

    name: str = "base"

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Return the model's raw text response."""

    def describe(self) -> str:
        return self.name


class EchoBackend(LLMBackend):
    """Returns a pre-supplied response. No network, no key, fully offline."""

    name = "echo"

    def __init__(self, canned_response: str | dict[str, Any]):
        if isinstance(canned_response, dict):
            canned_response = json.dumps(canned_response, indent=2)
        self._response = canned_response
        self.last_prompt: tuple[str, str] | None = None

    def complete(self, system: str, user: str) -> str:
        self.last_prompt = (system, user)
        return self._response


class ManualBackend(LLMBackend):
    """Writes the prompt to disk; reads the response back from disk.

    Flow:
      1. First call writes <workdir>/prompt.txt and raises with instructions.
      2. Human pastes the model's reply into <workdir>/response.json.
      3. Re-running picks the response up and continues.
    """

    name = "manual"

    def __init__(self, workdir: Path):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)

    @property
    def prompt_path(self) -> Path:
        return self.workdir / "prompt.txt"

    @property
    def response_path(self) -> Path:
        return self.workdir / "response.json"

    def complete(self, system: str, user: str) -> str:
        if self.response_path.exists():
            return self.response_path.read_text(encoding="utf-8")
        self.prompt_path.write_text(
            f"=== SYSTEM ===\n{system}\n\n=== USER ===\n{user}\n", encoding="utf-8"
        )
        raise ManualStepRequired(
            f"Prompt written to {self.prompt_path}\n"
            f"Paste the model's JSON reply into {self.response_path}, then re-run."
        )


class ManualStepRequired(RuntimeError):
    """Raised by ManualBackend when it needs a human to supply the response."""


class AnthropicBackend(LLMBackend):
    """Calls the Claude API."""

    name = "anthropic"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        api_key: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it, pass api_key=..., or use "
                "--backend manual to run without an API key."
            )

    def describe(self) -> str:
        return f"anthropic:{self.model}"

    def complete(self, system: str, user: str) -> str:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - env dependent
            raise RuntimeError(
                "The `anthropic` package is not installed. Run:\n"
                "    pip install anthropic\n"
                "or use --backend manual to run without it."
            ) from exc

        client = anthropic.Anthropic(api_key=self._api_key)
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in message.content if block.type == "text")


def get_backend(name: str, **kwargs: Any) -> LLMBackend:
    """Factory used by the CLI."""
    key = (name or "").lower()
    if key == "anthropic":
        return AnthropicBackend(**{k: v for k, v in kwargs.items() if k in {"model", "api_key", "max_tokens"}})
    if key == "manual":
        return ManualBackend(workdir=kwargs["workdir"])
    if key == "echo":
        return EchoBackend(kwargs["canned_response"])
    raise ValueError(f"Unknown backend {name!r}. Expected one of: anthropic, manual, echo.")
