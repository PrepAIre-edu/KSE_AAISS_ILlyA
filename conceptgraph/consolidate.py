"""REDUCE stage: the part that needs a view of the whole catalogue.

Three passes, cheapest first:
  R1  adjudicate lexical merge candidates            — high precision
  R2  free-form duplicate hunt over catalogue slices — recall for pairs worded
      differently, which no lexical filter can reach
  R3  bridge links, slice detail + global slug index — connects the components
      that per-unit extraction could never join
"""
from __future__ import annotations
import collections
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import Settings
from .graph import ConceptGraph, merge_candidates, norm_name
from .llm import LLM
from .prompts import (BRIDGE_SYSTEM, BRIDGE_USER, MERGE_SYSTEM, MERGE_USER,
                      PROPOSE_MERGE_SYSTEM, PROPOSE_MERGE_USER)
from .schemas import BridgeLinks, MergeAdjudication


def _card(g: ConceptGraph, slug: str, *, full: bool) -> str:
    c = g.concepts[slug]
    kind = c.get("kind") or (c["_kinds"][0] if c.get("_kinds") else "?")
    dom = c.get("domain") or (c["_domains"][0] if c.get("_domains") else "?")
    courses = ",".join(sorted(c["courses"]))
    al = "; ".join(sorted(c["aliases"])[:3])
    d = c.get("definition") or max(c.get("_defs") or [""], key=len)
    d = d[:260] if full else d[:120]
    return f"- {slug} | {c['name']} | {kind} | {dom} | {courses}" + \
           (f" | aliases: {al}" if al else "") + f"\n    {d}"


# ---------------------------------------------------------------- R1 + R2
def find_merges(s: Settings, llm: LLM, g: ConceptGraph) -> list[dict]:
    verdicts: list[dict] = []

    # R1 — adjudicate lexical candidates
    cands = merge_candidates(g, s.merge_candidate_cutoff, s.max_merge_pairs)
    chunks = [cands[i:i + s.merge_pairs_per_call]
              for i in range(0, len(cands), s.merge_pairs_per_call)]

    def adjudicate(chunk):
        blocks = []
        for a, b, sc, why in chunk:
            blocks.append(f"### PAIR {a}  <>  {b}   ({why}, score {sc})\n"
                          f"{_card(g, a, full=True)}\n{_card(g, b, full=True)}")
        return llm.structured(
            stage="merge-adjudicate", model=s.consolidate_model, system=MERGE_SYSTEM,
            prompt=MERGE_USER.format(n=len(chunk), pairs="\n\n".join(blocks)),
            out_model=MergeAdjudication).verdicts

    # R2 — free-form duplicate hunt, sliced by domain so related things co-occur
    by_dom: dict[str, list[str]] = collections.defaultdict(list)
    for slug, c in g.concepts.items():
        doms = c.get("_domains") or []
        by_dom[doms[0] if doms else "?"].append(slug)
    slices: list[list[str]] = []
    buf: list[str] = []
    for dom in sorted(by_dom):
        for slug in sorted(by_dom[dom]):
            buf.append(slug)
            if len(buf) >= s.bridge_slice_size * 2:
                slices.append(buf)
                buf = []
    if buf:
        slices.append(buf)

    def hunt(sl):
        cat = "\n".join(_card(g, x, full=True) for x in sl)
        return llm.structured(
            stage="merge-propose", model=s.consolidate_model, system=PROPOSE_MERGE_SYSTEM,
            prompt=PROPOSE_MERGE_USER.format(n=len(sl), catalogue=cat),
            out_model=MergeAdjudication).verdicts

    jobs = [("adj", c) for c in chunks] + [("hunt", sl) for sl in slices]
    with ThreadPoolExecutor(max_workers=s.max_workers) as pool:
        futs = {pool.submit(adjudicate if k == "adj" else hunt, payload): k
                for k, payload in jobs}
        for f in as_completed(futs):
            try:
                verdicts += [v.model_dump() for v in f.result()]
            except Exception as e:                                    # noqa: BLE001
                print(f"  merge pass failed — {e}")

    # collapse verdicts into merge groups via union-find on canonical choice
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    reasons: dict[tuple[str, str], str] = {}
    for v in verdicts:
        if not v.get("same"):
            continue
        a, b = v.get("slug_a"), v.get("slug_b")
        if a not in g.concepts or b not in g.concepts or a == b:
            continue
        can = v.get("canonical") if v.get("canonical") in (a, b) else a
        other = b if can == a else a
        ra, rb = find(can), find(other)
        if ra != rb:
            parent[rb] = ra
        reasons[(can, other)] = v.get("reason", "")

    groups: dict[str, list[str]] = collections.defaultdict(list)
    for slug in list(parent):
        r = find(slug)
        if slug != r:
            groups[r].append(slug)
    out = [{"canonical": can, "absorb": sorted(set(ab)),
            "reason": next((r for (c, o), r in reasons.items() if c == can), "")}
           for can, ab in groups.items() if ab]
    print(f"  merges: {len(cands)} candidates + {len(slices)} slices scanned "
          f"-> {len(out)} groups, {sum(len(m['absorb']) for m in out)} absorbed")
    return out


# ---------------------------------------------------------------- R3
def find_bridges(s: Settings, llm: LLM, g: ConceptGraph,
                 existing_links: list[dict]) -> list[dict]:
    slugs = sorted(g.concepts)
    index = "\n".join(
        f"{x} ({','.join(sorted(g.concepts[x]['courses']))}) — {g.concepts[x]['name']}"
        for x in slugs)

    touching: dict[str, list[str]] = collections.defaultdict(list)
    for l in existing_links:
        line = f"{l['src']} -{l['type']}-> {l['dst']}"
        touching[l["src"]].append(line)
        touching[l["dst"]].append(line)

    # slice by domain so each call sees a coherent neighbourhood
    by_dom: dict[str, list[str]] = collections.defaultdict(list)
    for x in slugs:
        c = g.concepts[x]
        by_dom[c.get("domain") or "?"].append(x)
    slices, buf = [], []
    for dom in sorted(by_dom):
        for x in sorted(by_dom[dom]):
            buf.append(x)
            if len(buf) >= s.bridge_slice_size:
                slices.append(buf)
                buf = []
    if buf:
        slices.append(buf)

    def one(sl):
        ex = sorted({ln for x in sl for ln in touching.get(x, [])})[:180]
        return llm.structured(
            stage="bridge", model=s.consolidate_model, system=BRIDGE_SYSTEM,
            prompt=BRIDGE_USER.format(
                k=s.bridge_links_per_slice,
                slice_detail="\n".join(_card(g, x, full=True) for x in sl),
                existing="\n".join(ex) or "(none)",
                index=index),
            out_model=BridgeLinks).links

    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=s.max_workers) as pool:
        futs = [pool.submit(one, sl) for sl in slices]
        for f in as_completed(futs):
            try:
                out += [l.model_dump() for l in f.result()]
            except Exception as e:                                    # noqa: BLE001
                print(f"  bridge slice failed — {e}")
    print(f"  bridges: {len(slices)} slices -> {len(out)} proposed links")
    return out
