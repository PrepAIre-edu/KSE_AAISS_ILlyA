# -*- coding: utf-8 -*-
"""`conceptgraph doctor` — check a local setup BEFORE spending hours on a run.

Six checks, cheap to expensive. Each one has failed for real during development,
which is why it is here rather than in a README as advice.

  1. backend reachable
  2. the named model is actually installed
  3. the model honours a JSON schema (Ollama `format` / OpenAI json_schema)
  4. the context window is big enough for the batch size in use
  5. EXTRACTION eval — can it pull concepts out of a syllabus row and quote it
     verbatim? Paraphrasing here silently destroys the evidence trail.
  6. MERGE eval — the decisive one. Six pairs that a lexical prefilter flags as
     near-identical and that MUST all be rejected. A model that merges
     supervised/unsupervised learning will collapse the graph with no error at
     all, so this is the check that decides whether a model is usable.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .config import Settings
from .prompts import EXTRACT_SYSTEM, MERGE_SYSTEM, MERGE_USER
from .schemas import BatchExtraction, MergeAdjudication

# ---- fixtures ---------------------------------------------------------------

# Verbatim rows from the KSE syllabi, with the concepts a competent extractor
# must find. Kept tiny so the check costs seconds even on a 7B model.
EXTRACT_FIXTURE = {
    "unit_id": "FIX-U1",
    "text": (
        "### FILE: fixture/syllabus.pdf  [handbook]\n\n"
        "Lecture 12\n"
        "Definite integral. Properties of the definite integral. "
        "Mean value theorem for integrals. Newton-Leibniz formula.\n"
        "Practice session 31\n"
    ),
    "must_find_any": [
        ["definite", "integral"],
        ["newton", "leibniz"],
        ["mean value", "theorem"],
    ],
}

# Every pair here is a FALSE positive of the lexical prefilter: high name
# similarity, opposite or sibling meaning. Correct verdict is same=False.
MERGE_FIXTURE = [
    ("supervised-learning", "Supervised Learning",
     "Learning a mapping from inputs to known target values using labelled examples.",
     "unsupervised-learning", "Unsupervised Learning",
     "Finding structure in data without target labels."),
    ("definite-integral", "Definite Integral",
     "A number attached to a function on an interval, interpretable as signed area.",
     "indefinite-integral", "Indefinite Integral",
     "The family of all antiderivatives of a function, differing by a constant."),
    ("l1-regularization", "L1 Regularization",
     "Penalising the sum of absolute weights, which drives some exactly to zero.",
     "l2-regularization", "L2 Regularization",
     "Penalising the sum of squared weights, shrinking them smoothly toward zero."),
    ("differentiable-function", "Differentiable Function",
     "A function possessing a derivative at every point of an interval.",
     "differential", "Differential of a Function",
     "The linear part of a function's increment; local linear approximation."),
    ("precision", "Precision",
     "The share of predicted positives that are actually positive.",
     "recall", "Recall",
     "The share of truly positive cases the model retrieves."),
    ("demand-curve", "Demand Curve",
     "Price of a good as a function of the quantity customers would buy.",
     "supply-curve", "Supply Curve",
     "Price of a good as a function of the quantity suppliers would supply."),
]


class _Ping(BaseModel):
    ok: bool = Field(description="always true")
    n: int = Field(ge=1, le=3, description="always 2")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    fatal: bool = False


@dataclass
class Result:
    checks: list = field(default_factory=list)

    def add(self, *a, **kw):
        self.checks.append(Check(*a, **kw))
        c = self.checks[-1]
        mark = "OK  " if c.passed else ("FAIL" if c.fatal else "WARN")
        print(f"  [{mark}] {c.name}" + (f" — {c.detail}" if c.detail else ""))
        return c.passed

    @property
    def fatal_failures(self):
        return [c for c in self.checks if not c.passed and c.fatal]


def _installed_models(host: str) -> list[str]:
    import urllib.request
    with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=15) as r:
        return [m["name"] for m in json.loads(r.read()).get("models", [])]


def run(s: Settings) -> int:
    print(f"conceptgraph doctor — backend={s.backend} model={s.extract_model}\n")
    res = Result()

    # ---- 1 & 2: reachable, model present ---------------------------------
    if s.backend == "ollama":
        host = s.base_url or "http://localhost:11434"
        try:
            models = _installed_models(host)
            res.add("Ollama reachable", True, f"{host}, {len(models)} model(s)")
        except Exception as e:                                    # noqa: BLE001
            res.add("Ollama reachable", False,
                    f"{host}: {e}. Start it with `ollama serve`.", fatal=True)
            return _verdict(res)
        want = s.extract_model
        hit = [m for m in models if m == want or m.split(":")[0] == want.split(":")[0]]
        if not res.add(f"model {want!r} installed", bool(hit),
                       (f"found {hit[0]}" if hit else
                        f"have {models}; run `ollama pull {want}`"),
                       fatal=not hit):
            return _verdict(res)
    else:
        res.add(f"backend {s.backend}", True, "connectivity checked by the first call")

    from .pipeline import _make_llm
    llm = _make_llm(s)

    # ---- 3: schema-enforced output ---------------------------------------
    try:
        p = llm.structured(stage="doctor", model=s.extract_model,
                           system="You return JSON only.",
                           prompt="Set ok to true and n to 2.",
                           out_model=_Ping, max_tokens=256)
        res.add("schema-enforced JSON output", p.n == 2,
                f"got ok={p.ok} n={p.n}" + ("" if p.n == 2 else " (expected n=2)"),
                fatal=False)
    except Exception as e:                                        # noqa: BLE001
        res.add("schema-enforced JSON output", False, str(e)[:160], fatal=True)
        return _verdict(res)

    # ---- 4: context vs batch --------------------------------------------
    need = s.batch_target_chars / 4
    res.add("context window vs batch size",
            need < s.local_num_ctx * 0.6,
            f"batch ~{need:,.0f} tok, num_ctx {s.local_num_ctx:,} "
            f"({need / s.local_num_ctx:.0%} of the window)")

    # ---- 5: extraction eval ---------------------------------------------
    sysmsg = EXTRACT_SYSTEM.format(min_c=2, max_c=6, hard_max=8, min_q=15, max_q=300,
                                   links_per=1.5)
    try:
        got = llm.structured(
            stage="doctor-extract", model=s.extract_model, system=sysmsg,
            prompt=("Extract concepts for 1 unit. Use exactly this unit_id: "
                    f"{EXTRACT_FIXTURE['unit_id']}\n\n{EXTRACT_FIXTURE['text']}"),
            out_model=BatchExtraction, max_tokens=2048)
        concepts = [c for u in got.units for c in u.concepts]
        names = " | ".join(c.name.lower() for c in concepts)
        found = sum(1 for toks in EXTRACT_FIXTURE["must_find_any"]
                    if all(t in names for t in toks))
        body = EXTRACT_FIXTURE["text"].lower()
        quotes = [o.quote for c in concepts for o in c.occurrences]
        verbatim = sum(1 for q in quotes
                       if " ".join(q.lower().split()) in " ".join(body.split()))
        res.add("extraction: concepts found", found >= 2,
                f"{found}/3 expected topics; got {len(concepts)} concept(s): "
                + ", ".join(c.slug for c in concepts)[:120])
        res.add("extraction: quotes verbatim", bool(quotes) and verbatim == len(quotes),
                f"{verbatim}/{len(quotes)} exact"
                + ("" if verbatim == len(quotes) else " — paraphrasing breaks the evidence trail"))
    except Exception as e:                                        # noqa: BLE001
        res.add("extraction eval", False, str(e)[:160], fatal=True)

    # ---- 6: merge eval, the decisive one --------------------------------
    blocks = []
    for a_s, a_n, a_d, b_s, b_n, b_d in MERGE_FIXTURE:
        blocks.append(f"### PAIR {a_s}  <>  {b_s}   (name similarity, score 0.93)\n"
                      f"- {a_s} | {a_n} | definition | ?\n    {a_d}\n"
                      f"- {b_s} | {b_n} | definition | ?\n    {b_d}")
    try:
        adj = llm.structured(
            stage="doctor-merge", model=s.consolidate_model, system=MERGE_SYSTEM,
            prompt=MERGE_USER.format(n=len(MERGE_FIXTURE), pairs="\n\n".join(blocks)),
            out_model=MergeAdjudication, max_tokens=3072)
        by = {}
        for v in adj.verdicts:
            by[tuple(sorted((v.slug_a or "", v.slug_b or "")))] = v.same
        wrong = []
        answered = 0
        for a_s, *_r, b_s, _bn, _bd in [(x[0], x[1], x[2], x[3], x[4], x[5])
                                        for x in MERGE_FIXTURE]:
            k = tuple(sorted((a_s, b_s)))
            if k in by:
                answered += 1
                if by[k]:
                    wrong.append(f"{a_s}={b_s}")
        res.add("merge: all 6 pairs answered", answered == len(MERGE_FIXTURE),
                f"{answered}/{len(MERGE_FIXTURE)}")
        res.add("merge: no false merges", not wrong,
                "clean — model can be trusted with consolidation" if not wrong
                else f"WRONGLY MERGED {len(wrong)}: {', '.join(wrong)}",
                fatal=bool(wrong))
    except Exception as e:                                        # noqa: BLE001
        res.add("merge eval", False, str(e)[:160], fatal=True)

    return _verdict(res)


def _verdict(res: Result) -> int:
    print()
    bad = res.fatal_failures
    warn = [c for c in res.checks if not c.passed and not c.fatal]
    if bad:
        print("VERDICT: not usable as configured.")
        for c in bad:
            print(f"  - {c.name}: {c.detail}")
        if any("MERGED" in (c.detail or "") for c in bad):
            print("\n  A model that merges opposites will collapse the graph silently.\n"
                  "  Either use a bigger model for consolidation, or keep extraction\n"
                  "  local and run `--consolidate-model` on a stronger backend.")
        return 1
    if warn:
        print("VERDICT: usable, with caveats.")
        for c in warn:
            print(f"  - {c.name}: {c.detail}")
        return 0
    print("VERDICT: ready. Run the pipeline.")
    return 0
