"""Deterministic graph algebra: canonicalise, dedupe, enforce the DAG.

No LLM here. Given raw per-unit extractions (and optionally a consolidation
decision set), produce the canonical concept graph. Being deterministic means
the same inputs always give the same graph, which makes the pipeline testable.
"""
from __future__ import annotations
import collections
import difflib
import re
from dataclasses import dataclass, field

from .schemas import SYMMETRIC

LINK_ORDER = {"prerequisite": 0, "part_of": 1, "applies_to": 2,
              "contrasts_with": 3, "related": 4}
STOPWORDS = {"the", "a", "an", "of", "and", "in", "for", "to", "as", "on"}


def norm_slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)


def norm_name(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return " ".join(w for w in s.split() if w not in STOPWORDS)


@dataclass
class ConceptGraph:
    concepts: dict = field(default_factory=dict)     # slug -> record
    links: list = field(default_factory=list)
    occurrences: list = field(default_factory=list)
    alias_of: dict = field(default_factory=dict)     # absorbed slug -> canonical
    notes: list = field(default_factory=list)

    def canonical(self, slug: str) -> str:
        seen = set()
        while slug in self.alias_of and slug not in seen:
            seen.add(slug)
            slug = self.alias_of[slug]
        return slug


def build(extractions: list[dict], unit_index: list[dict]) -> ConceptGraph:
    """Fold per-unit extractions into one graph, merging on exact slug."""
    uidx = {u["unit_id"]: u for u in unit_index}
    g = ConceptGraph()
    raw_occ, raw_links = [], []
    agg: dict[str, dict] = {}

    for ex in extractions:
        uid = ex["unit_id"]
        if uid not in uidx:
            g.notes.append(f"unknown unit {uid} — dropped")
            continue
        allowed = {f["rel_path"] for f in uidx[uid]["files"]}
        course = uidx[uid]["course"]

        for c in ex.get("concepts", []):
            slug = norm_slug(c.get("slug") or c.get("name", ""))
            if not slug:
                continue
            a = agg.setdefault(slug, {
                "slug": slug, "name": c.get("name") or slug.replace("-", " ").title(),
                "aliases": set(), "units": set(), "courses": set(),
                "_defs": [], "_diffs": [], "_kinds": [], "_domains": [],
            })
            a["_defs"].append((c.get("definition") or "").strip())
            a["_diffs"].append(int(c.get("difficulty") or 3))
            a["_kinds"].append(c.get("kind") or "definition")
            if c.get("domain"):
                a["_domains"].append(c["domain"])
            for al in c.get("aliases") or []:
                if al and norm_name(al) != norm_name(a["name"]):
                    a["aliases"].add(al.strip())
            a["units"].add(uid)
            a["courses"].add(course)

            for o in c.get("occurrences") or []:
                rp = o.get("rel_path")
                if rp not in allowed:
                    g.notes.append(f"{uid}/{slug}: rel_path outside unit -> {rp}")
                    continue
                raw_occ.append({
                    "concept_slug": slug, "unit_id": uid, "rel_path": rp,
                    "role": o.get("role") or "mentioned",
                    "quote": re.sub(r"\s+", " ", (o.get("quote") or "")).strip(),
                    "confidence": float(o.get("confidence") or 0.7),
                })

        for l in ex.get("links") or []:
            s, d = norm_slug(l.get("src", "")), norm_slug(l.get("dst", ""))
            if s and d and s != d and l.get("type") in LINK_ORDER:
                raw_links.append({"src": s, "dst": d, "type": l["type"],
                                  "strength": float(l.get("strength") or 0.5),
                                  "rationale": (l.get("rationale") or "").strip(),
                                  "origin": "extracted"})

    g.concepts = agg
    g._raw_occ, g._raw_links = raw_occ, raw_links               # type: ignore[attr-defined]
    return g


def _trigrams(s: str) -> set[str]:
    s = f"  {s}  "
    return {s[i:i + 3] for i in range(len(s) - 2)}


def merge_candidates(g: ConceptGraph, cutoff: float, limit: int) -> list[tuple[str, str, float, str]]:
    """Cheap pre-filter over three signals, so the LLM adjudicates O(candidates)
    pairs rather than the O(n^2) cross product:

      1. alias match      — one concept's name is the other's declared alias
      2. name similarity  — normalised-name sequence ratio >= cutoff
      3. definition overlap — trigram Jaccard on the definitions. Catches pairs
         worded completely differently ("computer as tool" / "AI as tool") that
         signals 1 and 2 both miss.
    """
    slugs = sorted(g.concepts)
    names = {s: norm_name(g.concepts[s]["name"]) or s.replace("-", " ") for s in slugs}
    alias = {s: {norm_name(a) for a in g.concepts[s]["aliases"]} for s in slugs}
    defs = {s: _trigrams(norm_name(max(g.concepts[s]["_defs"], key=len, default="")[:400]))
            for s in slugs}

    out: dict[tuple[str, str], tuple[float, str]] = {}

    def consider(a: str, b: str) -> None:
        key = (a, b) if a < b else (b, a)
        if key in out:
            return
        if names[a] in alias[b] or names[b] in alias[a]:
            out[key] = (1.0, "alias match")
            return
        r = difflib.SequenceMatcher(None, names[a], names[b]).ratio()
        if r >= cutoff:
            out[key] = (round(r, 3), "name similarity")
            return
        da, db = defs[a], defs[b]
        if da and db:
            j = len(da & db) / len(da | db)
            # definitions are long, so a high Jaccard is a strong signal
            if j >= 0.62:
                out[key] = (round(j, 3), "definition overlap")

    # bucket by shared token to avoid the full cross product
    buckets: dict[str, list[str]] = collections.defaultdict(list)
    for s in slugs:
        toks = set(names[s].split()) | {t for a in alias[s] for t in a.split()}
        for tok in toks:
            if len(tok) > 3:
                buckets[tok].append(s)
    for group in buckets.values():
        if len(group) > 60:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                consider(a, b)

    # definition-overlap pass, bucketed by domain so it stays cheap
    by_domain: dict[str, list[str]] = collections.defaultdict(list)
    for s in slugs:
        doms = g.concepts[s]["_domains"]
        by_domain[doms[0] if doms else "?"].append(s)
    for group in by_domain.values():
        if len(group) > 80:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                consider(a, b)

    ranked = [(a, b, sc, why) for (a, b), (sc, why) in out.items()]
    ranked.sort(key=lambda x: -x[2])
    return ranked[:limit]


def apply_merges(g: ConceptGraph, merges: list[dict]) -> int:
    """merges: [{canonical, absorb:[slug,...], reason}]"""
    n = 0
    for m in merges:
        can = g.canonical(norm_slug(m.get("canonical", "")))
        if can not in g.concepts:
            g.notes.append(f"merge skipped, missing canonical {can}")
            continue
        for a in m.get("absorb", []):
            a = g.canonical(norm_slug(a))
            if a == can or a not in g.concepts:
                continue
            src, tgt = g.concepts.pop(a), g.concepts[can]
            tgt["aliases"] |= src["aliases"] | {src["name"]}
            tgt["units"] |= src["units"]
            tgt["courses"] |= src["courses"]
            for k in ("_defs", "_diffs", "_kinds", "_domains"):
                tgt[k] += src[k]
            g.alias_of[a] = can
            n += 1
    return n


def finalise(g: ConceptGraph, bridge_links: list[dict] | None = None) -> ConceptGraph:
    """Resolve aliases, dedupe occurrences and links, break prerequisite cycles."""
    for rec in g.concepts.values():
        defs = [d for d in rec.pop("_defs") if d]
        rec["definition"] = max(defs, key=len) if defs else ""
        diffs = rec.pop("_diffs")
        rec["difficulty"] = round(sum(diffs) / len(diffs)) if diffs else 3
        kinds = rec.pop("_kinds")
        rec["kind"] = collections.Counter(kinds).most_common(1)[0][0] if kinds else "definition"
        doms = rec.pop("_domains")
        rec["domain"] = collections.Counter(doms).most_common(1)[0][0] if doms else None
        rec["aliases"] = sorted(a for a in rec["aliases"] if a)
        rec["units"] = sorted(rec["units"])
        rec["courses"] = sorted(rec["courses"])
        rec["n_units"], rec["n_courses"] = len(rec["units"]), len(rec["courses"])

    # occurrences
    occ, seen = [], set()
    for o in g._raw_occ:                                        # type: ignore[attr-defined]
        o = dict(o)
        o["concept_slug"] = g.canonical(o["concept_slug"])
        if o["concept_slug"] not in g.concepts or not o["quote"]:
            continue
        key = (o["concept_slug"], o["rel_path"], o["quote"][:80])
        if key in seen:
            continue
        seen.add(key)
        occ.append(o)
    g.occurrences = occ

    # links
    lmap: dict[tuple, dict] = {}

    def add(l: dict) -> bool:
        s, d = g.canonical(norm_slug(l["src"])), g.canonical(norm_slug(l["dst"]))
        if s not in g.concepts or d not in g.concepts or s == d:
            return False
        t = l["type"]
        if t in SYMMETRIC:
            s, d = sorted((s, d))
        key = (s, d, t)
        prev = lmap.get(key)
        if prev is None or l["strength"] > prev["strength"]:
            lmap[key] = {"src": s, "dst": d, "type": t, "strength": l["strength"],
                         "rationale": l.get("rationale", ""),
                         "origin": l.get("origin", "extracted")}
        return True

    for l in g._raw_links:                                      # type: ignore[attr-defined]
        add(l)
    dropped = 0
    for l in bridge_links or []:
        if not add({**l, "origin": "bridge"}):
            dropped += 1
    if dropped:
        g.notes.append(f"{dropped} bridge links referenced unknown slugs")

    # a->b and b->a of the same asymmetric type cannot both hold: keep stronger
    for (s, d, t) in list(lmap):
        if t in SYMMETRIC or (s, d, t) not in lmap or (d, s, t) not in lmap:
            continue
        a, b = lmap[(s, d, t)], lmap[(d, s, t)]
        del lmap[(d, s, t) if a["strength"] >= b["strength"] else (s, d, t)]

    # prerequisite must be a DAG or no teaching order exists; drop weakest edge
    removed = []
    while True:
        adj = collections.defaultdict(list)
        for (s, d, t) in lmap:
            if t == "prerequisite":
                adj[s].append(d)
        colour, cycle = collections.defaultdict(int), []

        def dfs(u):
            if cycle:
                return
            colour[u] = 1
            for v in adj[u]:
                if cycle:
                    break
                if colour[v] == 1:
                    stack_path = path[path.index(v):] + [v] if v in path else [v, u, v]
                    cycle.extend(stack_path)
                    break
                if colour[v] == 0:
                    path.append(v)
                    dfs(v)
                    path.pop()
            colour[u] = 2

        for n0 in list(adj):
            if colour[n0] == 0 and not cycle:
                path = [n0]
                dfs(n0)
        if not cycle:
            break
        edges = [(cycle[i], cycle[i + 1]) for i in range(len(cycle) - 1)]
        edges = [e for e in edges if (e[0], e[1], "prerequisite") in lmap]
        if not edges:
            break
        weak = min(edges, key=lambda e: lmap[(e[0], e[1], "prerequisite")]["strength"])
        del lmap[(weak[0], weak[1], "prerequisite")]
        removed.append(weak)
    if removed:
        g.notes.append(f"broke {len(removed)} prerequisite cycle(s): {removed[:5]}")

    g.links = sorted(lmap.values(), key=lambda l: (LINK_ORDER[l["type"]], l["src"], l["dst"]))

    # denormalised convenience fields
    occ_by = collections.Counter(o["concept_slug"] for o in g.occurrences)
    files_by = collections.defaultdict(set)
    for o in g.occurrences:
        files_by[o["concept_slug"]].add(o["rel_path"])
    deg = collections.Counter()
    for l in g.links:
        deg[l["src"]] += 1
        deg[l["dst"]] += 1
    for s, rec in g.concepts.items():
        rec["occurrence_count"] = occ_by[s]
        rec["files"] = sorted(files_by[s])
        rec["degree"] = deg[s]
    return g


def components(g: ConceptGraph) -> list[list[str]]:
    adj = collections.defaultdict(set)
    for l in g.links:
        adj[l["src"]].add(l["dst"])
        adj[l["dst"]].add(l["src"])
    seen, out = set(), []
    for n in g.concepts:
        if n in seen:
            continue
        stack, comp = [n], []
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            comp.append(x)
            stack += [y for y in adj[x] if y not in seen]
        out.append(comp)
    return sorted(out, key=len, reverse=True)
