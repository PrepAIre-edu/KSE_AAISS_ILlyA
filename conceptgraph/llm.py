"""Anthropic client wrapper.

Three things this buys us:
  * structured output — every call is forced through a tool_use input_schema,
    so we never regex a JSON blob out of prose or strip markdown fences;
  * resumability — responses are cached on sha256(model+prompt+schema), so a
    crashed or interrupted run re-reads instead of re-paying;
  * accounting — token usage and USD cost per stage.
"""
from __future__ import annotations
import hashlib
import json
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .config import PRICES, Settings
from .schemas import tool_schema

T = TypeVar("T", bound=BaseModel)


@dataclass
class Usage:
    calls: int = 0
    cached: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0
    by_stage: dict = field(default_factory=dict)

    def add(self, stage: str, model: str, ti: int, to: int, cached: bool):
        self.calls += 1
        if cached:
            self.cached += 1
        self.input_tokens += ti
        self.output_tokens += to
        s = self.by_stage.setdefault(stage, {"calls": 0, "in": 0, "out": 0, "usd": 0.0})
        s["calls"] += 1
        s["in"] += ti
        s["out"] += to
        s["usd"] += cost(model, ti, to)

    @property
    def usd(self) -> float:
        return round(sum(s["usd"] for s in self.by_stage.values()), 4)

    def report(self) -> str:
        lines = [f"{self.calls} calls ({self.cached} from cache), "
                 f"{self.input_tokens:,} in / {self.output_tokens:,} out, "
                 f"~${self.usd}"]
        for st, s in sorted(self.by_stage.items()):
            lines.append(f"  {st:<22} {s['calls']:>3} calls  "
                         f"{s['in']:>9,} in  {s['out']:>7,} out  ${s['usd']:.3f}")
        return "\n".join(lines)


def cost(model: str, ti: int, to: int) -> float:
    p = PRICES.get(model)
    if not p:
        return 0.0
    return ti / 1e6 * p["in"] + to / 1e6 * p["out"]


class LLM:
    def __init__(self, settings: Settings):
        import anthropic
        self.s = settings
        self.client = anthropic.Anthropic(api_key=settings.api_key)
        self.usage = Usage()
        self._lock = threading.Lock()

    # -- cache ------------------------------------------------------------
    def _key(self, model: str, system: str, prompt: str, schema: dict) -> str:
        h = hashlib.sha256()
        for part in (model, system, prompt, json.dumps(schema, sort_keys=True)):
            h.update(part.encode())
            h.update(b"\x00")
        return h.hexdigest()[:32]

    def structured(self, *, stage: str, model: str, system: str, prompt: str,
                   out_model: type[T], max_tokens: int | None = None) -> T:
        """One call that must return an instance of out_model."""
        schema = tool_schema(out_model)
        tool_name = "emit_" + out_model.__name__.lower()
        cache = Path(self.s.cache_dir) / f"{stage}-{self._key(model, system, prompt, schema)}.json"

        if cache.exists():
            try:
                payload = json.loads(cache.read_text(encoding="utf-8"))
                obj = out_model.model_validate(payload["data"])
                with self._lock:
                    self.usage.add(stage, model, payload.get("in", 0), payload.get("out", 0), True)
                return obj
            except (ValidationError, json.JSONDecodeError, KeyError):
                cache.unlink(missing_ok=True)

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        last: Exception | None = None

        for attempt in range(self.s.max_retries):
            try:
                r = self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens or self.s.max_tokens_out,
                    temperature=self.s.temperature,
                    system=system,
                    tools=[{"name": tool_name,
                            "description": f"Return the result as {out_model.__name__}.",
                            "input_schema": schema}],
                    tool_choice={"type": "tool", "name": tool_name},
                    messages=messages,
                )
                block = next(b for b in r.content if b.type == "tool_use")
                obj = out_model.model_validate(block.input)
                ti, to = r.usage.input_tokens, r.usage.output_tokens
                cache.write_text(json.dumps({"data": block.input, "in": ti, "out": to},
                                            ensure_ascii=False), encoding="utf-8")
                with self._lock:
                    self.usage.add(stage, model, ti, to, False)
                return obj

            except ValidationError as e:
                # the model produced the wrong shape: show it the errors and retry
                last = e
                with self._lock:
                    self.usage.retries += 1
                messages = [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": json.dumps(
                        getattr(block, "input", {}), ensure_ascii=False)[:4000]},
                    {"role": "user", "content":
                        "That failed schema validation:\n"
                        f"{e.errors()[:12]}\nReturn a corrected result."},
                ]
            except Exception as e:                                   # noqa: BLE001
                last = e
                with self._lock:
                    self.usage.retries += 1
                if attempt == self.s.max_retries - 1:
                    break
                time.sleep(min(2 ** attempt + random.random(), 30))

        raise RuntimeError(f"{stage}: failed after {self.s.max_retries} attempts: {last}")
