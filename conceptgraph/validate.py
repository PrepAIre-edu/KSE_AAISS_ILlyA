"""Quality gate. Runs after the graph is built and before anything is exported.

The check that matters most is quote verbatimness: if the model paraphrased, the
concept has no real evidence and the whole "every concept links to a file" promise
is hollow. A low rate almost always means text extraction degraded, not that the
model misbehaved.
"""
from __future__ import annotations
import collections
import json
import re
from pathlib import Path

from .adapters import SourceFile
from .graph import ConceptGraph, components
from .textract import normalise


def _fold(s: str) -> str:
    """Aggressive fold: strips punctuation, case, hyphenation and whitespace so a
    quote still matches text that PDF extraction broke across a column or page."""
    s = normalise(s)
    s = re.sub(r"-\s*\n\s*", "", s)
    s = re.sub(r"[^0-9a-z]+", " ", s.lower())
    return " ".join(s.split())


def run(g: ConceptGraph, files: list[SourceFile], text_dir: Path,
        unit_index: list[dict], settings) -> dict:
    by_rel = {f.rel_path: f for f in files}
    cache: dict[str, str] = {}

    def body(rel: str) -> str:
        f = by_rel.get(rel)
        if not f:
            return ""
        if f.text_key not in cache:
            p = text_dir / f.text_key
            cache[f.text_key] = _fold(p.read_text(encoding="utf-8", errors="replace")) \
                if p.exists() else ""
        return cache[f.text_key]

    verified, unverified = 0, []
    for o in g.occurrences:
        q = _fold(o["quote"])
        if q and q in body(o["rel_path"]):
            verified += 1
        else:
            unverified.append({"concept_slug": o["concept_slug"],
                               "rel_path": o["rel_path"], "quote": o["quote"][:160]})
    total = verified + len(unverified)
    quote_rate = verified / total if total else 0.0

    per_unit = collections.Counter()
    for c in g.concepts.values():
        for u in c["units"]:
            per_unit[u] += 1
    expected = {u["unit_id"] for u in unit_index}
    covered = set(per_unit)
    out_of_range = sorted((u, n) for u, n in per_unit.items()
                          if not settings.min_concepts_per_unit <= n <= settings.max_concepts_per_unit)

    known = set(g.concepts)
    dangling = [l for l in g.links if l["src"] not in known or l["dst"] not in known]
    orphans = [s for s, c in g.concepts.items() if not c["files"]]
    thin = [s for s, c in g.concepts.items() if len(c["definition"]) < 20]
    isolated = [c[0] for c in components(g) if len(c) == 1]

    # prerequisite acyclicity (finalise() guarantees it; verify independently)
    adj = collections.defaultdict(list)
    for l in g.links:
        if l["type"] == "prerequisite":
            adj[l["src"]].append(l["dst"])
    colour, cyclic = collections.defaultdict(int), False

    def dfs(u):
        nonlocal cyclic
        colour[u] = 1
        for v in adj[u]:
            if colour[v] == 1:
                cyclic = True
            elif colour[v] == 0:
                dfs(v)
        colour[u] = 2
    for n in list(adj):
        if colour[n] == 0:
            dfs(n)

    comps = components(g)
    report = {
        "concepts": len(g.concepts),
        "links": len(g.links),
        "occurrences": len(g.occurrences),
        "link_types": dict(collections.Counter(l["type"] for l in g.links)),
        "bridge_links": sum(1 for l in g.links if l.get("origin") == "bridge"),
        "cross_course_links": sum(
            1 for l in g.links
            if set(g.concepts[l["src"]]["courses"]) != set(g.concepts[l["dst"]]["courses"])),
        "multi_course_concepts": sum(1 for c in g.concepts.values() if c["n_courses"] > 1),
        "quote_verbatim_rate": round(quote_rate, 4),
        "quotes_verified": verified,
        "quotes_unverified": len(unverified),
        "unit_coverage": round(len(covered & expected) / len(expected), 4) if expected else 0.0,
        "units_expected": len(expected),
        "units_covered": len(covered & expected),
        "units_empty": sorted(expected - covered),
        "units_out_of_range": out_of_range,
        "concepts_per_unit": {
            "min": min(per_unit.values()) if per_unit else 0,
            "max": max(per_unit.values()) if per_unit else 0,
            "median": sorted(per_unit.values())[len(per_unit) // 2] if per_unit else 0},
        "components": len(comps),
        "component_sizes": [len(c) for c in comps[:10]],
        "isolated_concepts": isolated,
        "dangling_links": len(dangling),
        "orphan_concepts": orphans,
        "thin_definitions": thin,
        "prerequisite_acyclic": not cyclic,
        "graph_notes": g.notes,
        "unverified_quotes": unverified,
    }

    failures = []
    if quote_rate < settings.min_quote_verbatim_rate:
        failures.append(f"quote verbatim rate {quote_rate:.1%} < "
                        f"{settings.min_quote_verbatim_rate:.0%}")
    if report["unit_coverage"] < settings.min_unit_coverage:
        failures.append(f"unit coverage {report['unit_coverage']:.1%} < "
                        f"{settings.min_unit_coverage:.0%}")
    if cyclic:
        failures.append("prerequisite graph contains a cycle")
    if dangling:
        failures.append(f"{len(dangling)} dangling links")
    if orphans:
        failures.append(f"{len(orphans)} concepts with no source file")
    report["gate_failures"] = failures
    report["gate_passed"] = not failures
    return report


def summary(r: dict) -> str:
    L = [
        f"concepts {r['concepts']} | links {r['links']} "
        f"({r['bridge_links']} bridge, {r['cross_course_links']} cross-course) "
        f"| occurrences {r['occurrences']}",
        f"concepts/unit min {r['concepts_per_unit']['min']} "
        f"median {r['concepts_per_unit']['median']} max {r['concepts_per_unit']['max']}"
        f"  ({len(r['units_out_of_range'])} units outside range)",
        f"quotes verbatim {r['quotes_verified']}/"
        f"{r['quotes_verified'] + r['quotes_unverified']} = {r['quote_verbatim_rate']:.1%}",
        f"units covered {r['units_covered']}/{r['units_expected']}",
        f"components {r['components']} {r['component_sizes']} "
        f"| isolated {len(r['isolated_concepts'])}",
        f"prerequisite DAG: {'OK' if r['prerequisite_acyclic'] else 'CYCLE'}",
        f"GATE: {'PASSED' if r['gate_passed'] else 'FAILED — ' + '; '.join(r['gate_failures'])}",
    ]
    return "\n".join("  " + x for x in L)
