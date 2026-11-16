"""LLM provider chain: Gemini first, then OpenRouter free models.

Both speak the OpenAI chat-completions format, so one client class serves both.
On a rate limit, timeout or any error we move to the next model in the chain.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

from openai import OpenAI

log = logging.getLogger("clinic.llm")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
COOLDOWN_SECONDS = 45  # skip a model for this long after it fails


class LLMUnavailable(Exception):
    pass


@dataclass
class Target:
    provider: str
    model: str
    client: OpenAI
    cooldown_until: float = 0.0

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass
class LLMChain:
    targets: list[Target] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "LLMChain":
        timeout = float(os.getenv("LLM_TIMEOUT", "10"))
        targets: list[Target] = []

        def add(provider: str, key_var: str, models_var: str, base_url: str) -> None:
            key = (os.getenv(key_var) or "").strip()
            models = [m.strip() for m in (os.getenv(models_var) or "").split(",") if m.strip()]
            if not key or not models:
                return
            client = OpenAI(api_key=key, base_url=base_url, timeout=timeout, max_retries=0)
            targets.extend(Target(provider, m, client) for m in models)

        add("gemini", "GEMINI_API_KEY", "GEMINI_MODELS", GEMINI_BASE_URL)
        add("openrouter", "OPENROUTER_API_KEY", "OPENROUTER_MODELS", OPENROUTER_BASE_URL)
        return cls(targets)

    def chat(self, messages: list[dict], tools: list[dict]) -> tuple[dict, str]:
        """Return (assistant message as a dict, label of the model that answered)."""
        if not self.targets:
            raise LLMUnavailable("No LLM is configured. Add API keys and model names to .env.")
        now = started = time.time()
        # Models in cooldown go to the back of the queue rather than being dropped.
        order = sorted(self.targets, key=lambda t: t.cooldown_until > now)
        last_error: Exception | None = None
        # A 503 ("model overloaded") is usually gone a moment later, so each model gets one quick retry for it.
        attempts = [(t, n) for t in order for n in (1, 2)]
        skip: Target | None = None
        for target, attempt in attempts:
            if target is skip or (attempt == 2 and target.cooldown_until <= started):
                continue  # second attempt only happens after a failed first one
            try:
                if attempt == 2:
                    time.sleep(0.5)
                resp = target.client.chat.completions.create(
                    model=target.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.2,
                )
                msg = resp.choices[0].message
                out: dict = {"role": "assistant", "content": msg.content or ""}
                if msg.tool_calls:
                    out["tool_calls"] = [tc.model_dump(exclude_none=True) for tc in msg.tool_calls]
                elif not out["content"].strip():
                    raise ValueError("empty reply")
                target.cooldown_until = 0.0
                return out, target.label
            except Exception as err:  # rate limit, timeout, bad response...
                last_error = err
                target.cooldown_until = time.time() + COOLDOWN_SECONDS
                if getattr(err, "status_code", None) != 503:
                    skip = target  # rate limit, timeout, bad model name: no point retrying now
                log.warning("LLM %s failed (%s: %s); trying the next model", target.label, type(err).__name__, str(err)[:200])
        raise LLMUnavailable(f"All models failed. Last error: {last_error}")
