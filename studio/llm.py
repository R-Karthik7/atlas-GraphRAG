"""LLM access with usage metering.

``get_llm()`` returns a Groq-backed client when ``GROQ_API_KEY`` is set and the
``groq`` package is installed, otherwise ``None``; every feature has an offline
fallback, so the application is fully usable without a key.
"""

import contextvars
import logging
import os
import random
import threading
import time

logger = logging.getLogger("atlas.llm")

DEFAULT_MODEL = "openai/gpt-oss-120b"
# tried in order when the configured model has been retired (Groq returns 404 "model not found")
FALLBACK_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile",
                   "llama-3.1-8b-instant"]
_meter_var = contextvars.ContextVar("atlas_meter", default=None)


class Meter:
    """Accumulates token usage and spend for one request (or one evaluation case)."""

    def __init__(self):
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0
        self.cost = 0.0
        self.errors = 0

    def add(self, tin, tout, cost):
        self.tokens_in += tin
        self.tokens_out += tout
        self.cost += cost
        self.calls += 1

    def as_dict(self):
        return {"tokens_in": self.tokens_in, "tokens_out": self.tokens_out, "calls": self.calls,
                "cost": round(self.cost, 6)}


class metered:
    def __enter__(self):
        self.meter = Meter()
        self.token = _meter_var.set(self.meter)
        return self.meter

    def __exit__(self, *exc):
        _meter_var.reset(self.token)


def estimate_tokens(text):
    return max(1, int(len(text or "") / 4))


def prices():
    def env(name, default):
        try:
            return float(os.getenv(name, default))
        except ValueError:
            return default
    return env("LLM_PRICE_IN_PER_M", 0.075), env("LLM_PRICE_OUT_PER_M", 0.30)


class GroqLLM:
    name = "groq"

    def __init__(self, api_key, model=None, client=None, max_retries=4):
        self.model = model or os.getenv("GROQ_MODEL", DEFAULT_MODEL)
        self.max_retries = max_retries
        self.effort = os.getenv("GROQ_REASONING_EFFORT") or None
        if client is None:
            from groq import Groq
            client = Groq(api_key=api_key, timeout=60)
        self.client = client

    @staticmethod
    def _retryable(error):
        status = getattr(error, "status_code", None)
        return status in {408, 409, 429, 500, 502, 503, 504} or type(error).__name__ in {
            "APIConnectionError", "APITimeoutError"}

    @staticmethod
    def _model_missing(error):
        text = str(error).lower()
        return getattr(error, "status_code", None) == 404 or type(error).__name__ == "NotFoundError" or "model_not_found" in text

    def _switch_model(self):
        """The configured model no longer exists: pick the first available replacement."""
        tried = getattr(self, "_dead", set())
        tried.add(self.model)
        self._dead = tried
        try:
            available = {m.id for m in self.client.models.list().data}
        except Exception:
            available = set(FALLBACK_MODELS)
        for cand in FALLBACK_MODELS:
            if cand in available and cand not in tried:
                logger.warning("Groq model %s is unavailable; switching to %s", self.model, cand)
                self.model = cand
                return True
        return False

    def complete(self, messages, temperature=0.2, max_tokens=1500):
        tokens, attempt = max_tokens, 0
        while True:
            req = {"model": self.model, "messages": messages, "temperature": temperature,
                   "max_completion_tokens": tokens}
            if self.effort:
                req["reasoning_effort"] = self.effort
            try:
                resp = self.client.chat.completions.create(**req)
            except Exception as error:
                if self._model_missing(error) and self._switch_model():
                    continue
                if attempt < self.max_retries and self._retryable(error):
                    delay = min(2 ** attempt + random.random(), 20)
                    logger.warning("LLM call failed (%s), retry in %.1fs", type(error).__name__, delay)
                    time.sleep(delay)
                    attempt += 1
                    continue
                meter = _meter_var.get()
                if meter:
                    meter.errors += 1
                raise
            choice = resp.choices[0]
            text = (choice.message.content or "").strip()
            self._account(messages, text, getattr(resp, "usage", None))
            if text or getattr(choice, "finish_reason", None) != "length" or tokens >= max_tokens * 4:
                return text
            tokens *= 2

    def _account(self, messages, text, usage):
        tin = getattr(usage, "prompt_tokens", None)
        tout = getattr(usage, "completion_tokens", None)
        if tin is None:
            tin = sum(estimate_tokens(m["content"]) for m in messages)
        if tout is None:
            tout = estimate_tokens(text)
        pin, pout = prices()
        meter = _meter_var.get()
        if meter:
            meter.add(tin, tout, (tin * pin + tout * pout) / 1e6)


_llm = None
_lock = threading.Lock()


def get_llm():
    global _llm
    with _lock:
        if _llm is None:
            key = os.getenv("GROQ_API_KEY")
            if not key or os.getenv("ATLAS_OFFLINE") == "1":
                _llm = False
            else:
                try:
                    _llm = GroqLLM(key)
                except Exception as error:      # package missing, bad config ...
                    logger.warning("Groq unavailable (%s); using offline mode", error)
                    _llm = False
        return _llm or None


def set_llm(llm):
    """Install a client (tests, or switching modes)."""
    global _llm
    with _lock:
        _llm = llm if llm is not None else False
