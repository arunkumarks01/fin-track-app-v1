"""Mock LLM — scripted structured responses with injectable failure modes.

The real provider abstraction (Phase 3) must fail closed on every one of these modes;
this fake makes each mode reproducible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class LLMMode(StrEnum):
    OK = "ok"
    TIMEOUT = "timeout"
    ERROR = "error"
    MALFORMED = "malformed"  # returns non-JSON text
    SCHEMA_MISMATCH = "schema_mismatch"  # valid JSON, wrong shape


class FakeLLMTimeout(TimeoutError):
    pass


class FakeLLMError(RuntimeError):
    pass


@dataclass
class FakeLLM:
    response: dict[str, Any] = field(default_factory=dict)
    mode: LLMMode = LLMMode.OK
    prompts: list[str] = field(default_factory=list)

    def complete(self, prompt: str) -> str:
        """Return raw model text. Parsing/validation is the caller's job."""
        self.prompts.append(prompt)
        match self.mode:
            case LLMMode.TIMEOUT:
                raise FakeLLMTimeout("scripted timeout")
            case LLMMode.ERROR:
                raise FakeLLMError("scripted provider error")
            case LLMMode.MALFORMED:
                return "Sure! Here is the sentiment: positive-ish"
            case LLMMode.SCHEMA_MISMATCH:
                return json.dumps({"unexpected": True})
            case LLMMode.OK:
                return json.dumps(self.response)
