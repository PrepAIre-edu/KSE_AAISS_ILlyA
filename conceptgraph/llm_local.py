# -*- coding: utf-8 -*-
"""Free LLM backends, drop-in for llm.LLM.

Both expose the same `.structured(stage=, model=, system=, prompt=, out_model=)`
signature as the Anthropic client, so extract.py and consolidate.py work
unchanged — only the object you construct differs.

    OllamaLLM   — fully local, free forever, no key, no data leaving the machine.
                  Ollama enforces a JSON schema through its `format` parameter,
                  so structured output survives even on small models.
    OpenAILLM   — any OpenAI-compatible endpoint with a free tier
                  (Gemini's compat layer, Groq, Mistral, OpenRouter free models).
                  Uses json_schema response_format.

Caveat that matters: small local models are noticeably worse at the one thing
that actually needs judgement here — deciding *link types* and refusing bad
merges. Run `validate` after every change and watch the prerequisite root count.
"""
from __future__ import annotations
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .config import Settings
from .llm import Usage
from .schemas import tool_schema

T = TypeVar("T", bound=BaseModel)


class _Cached:
    """Shared disk cache + retry loop; subclasses implement _call()."""

    def __init__(self, settings: Settings, tag: str):
        self.s = settings
        self.tag = tag
        self.usage = Usage()
        self._lock = threading.Lock()

    def _key(self, model, system, prompt, schema) -> str:
        h = hashlib.sha256()
        for part in (self.tag, model, system, prompt, json.dumps(schema, sort_keys=True)):
            h.update(part.encode()); h.update(b"\x00")
        return h.hexdigest()[:32]

    def _call(self, model: str, system: str, prompt: str, schema: dict,
              max_tokens: int) -> tuple[dict, int, int]:
        raise NotImplementedError

    def structured(self, *, stage: str, model: str, system: str, prompt: str,
                   out_model: type[T], max_tokens: int | None = None) -> T:
        schema = tool_schema(out_model)
        cache = Path(self.s.cache_dir) / f"{stage}-{self._key(model, system, prompt, schema)}.json"
        if cache.exists():
            try:
                pl = json.loads(cache.read_text(encoding="utf-8"))
                obj = out_model.model_validate(pl["data"])
                with self._lock:
                    self.usage.add(stage, model, pl.get("in", 0), pl.get("out", 0), True)
                return obj
            except (ValidationError, json.JSONDecodeError, KeyError):
                cache.unlink(missing_ok=True)

        last: Exception | None = None
        p = prompt
        for attempt in range(self.s.max_retries):
            try:
                data, ti, to = self._call(model, system, p, schema,
                                          max_tokens or self.s.max_tokens_out)
                obj = out_model.model_validate(data)
                cache.write_text(json.dumps({"data": data, "in": ti, "out": to},
                                            ensure_ascii=False), encoding="utf-8")
                with self._lock:
                    self.usage.add(stage, model, ti, to, False)
                return obj
            except ValidationError as e:
                last = e
                with self._lock:
                    self.usage.retries += 1
                p = (prompt + "\n\nYour previous answer failed schema validation:\n"
                     + json.dumps(e.errors()[:8], default=str)[:1500]
                     + "\nReturn only valid JSON matching the schema.")
            except Exception as e:                                    # noqa: BLE001
                last = e
                with self._lock:
                    self.usage.retries += 1
                if attempt == self.s.max_retries - 1:
                    break
                time.sleep(2 ** attempt)
        raise RuntimeError(f"{stage}: {self.tag} failed after {self.s.max_retries}: {last}")


class OllamaLLM(_Cached):
    """Local models. `ollama pull qwen2.5:14b-instruct` then point --extract-model at it.

    Ollama takes a full JSON Schema in `format`, which is how we keep the same
    hard output contract we get from Anthropic tool_use.
    """

    def __init__(self, settings: Settings, host: str = "http://localhost:11434"):
        super().__init__(settings, "ollama")
        self.host = host.rstrip("/")

    def _call(self, model, system, prompt, schema, max_tokens):
        import urllib.request
        body = json.dumps({
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "stream": False,
            "format": schema,                      # schema-enforced decoding
            "options": {"temperature": self.s.temperature,
                        "num_predict": max_tokens,
                        "num_ctx": self.s.local_num_ctx},
        }).encode()
        req = urllib.request.Request(f"{self.host}/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1800) as r:
            out = json.loads(r.read())
        return (json.loads(out["message"]["content"]),
                out.get("prompt_eval_count", 0), out.get("eval_count", 0))


class OpenAILLM(_Cached):
    """Any OpenAI-compatible endpoint. Free tiers worth trying, in this order:

        Gemini  base_url=https://generativelanguage.googleapis.com/v1beta/openai
        Groq    base_url=https://api.groq.com/openai/v1
        Mistral base_url=https://api.mistral.ai/v1

    Read the provider's terms before using one on KSE material: several free
    tiers reserve the right to train on your prompts.
    """

    def __init__(self, settings: Settings, base_url: str, api_key: str):
        super().__init__(settings, f"openai:{base_url}")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _call(self, model, system, prompt, schema, max_tokens):
        import urllib.request
        body = json.dumps({
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "temperature": self.s.temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "result", "strict": True,
                                                "schema": schema}},
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        with urllib.request.urlopen(req, timeout=600) as r:
            out = json.loads(r.read())
        u = out.get("usage", {})
        return (json.loads(out["choices"][0]["message"]["content"]),
                u.get("prompt_tokens", 0), u.get("completion_tokens", 0))


def make(settings: Settings, backend: str, **kw):
    if backend == "anthropic":
        from .llm import LLM
        return LLM(settings)
    if backend == "ollama":
        return OllamaLLM(settings, **kw)
    if backend == "openai":
        return OpenAILLM(settings, **kw)
    raise ValueError(f"unknown backend {backend!r}")
