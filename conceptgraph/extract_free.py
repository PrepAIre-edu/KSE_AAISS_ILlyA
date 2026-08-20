# -*- coding: utf-8 -*-
"""Free extraction path: deterministic syllabus parsing, zero API calls.

Produces the same `extractions.json` shape the LLM MAP stage produces, so every
downstream stage (graph, validate, export) is untouched. What it fills in:
    slug, name, occurrences (verbatim quote + rel_path)
What it leaves for a later pass:
    definition (empty), kind (guessed from wording), difficulty (from the
    syllabus difficulty key if present, else 3), links (none).
"""
from __future__ import annotations
import json
import re
from pathlib import Path

from .adapters import SourceFile
from .syllabus import extract as syllabus_extract

# crude but useful: the wording of a topic name hints at its kind
KIND_HINTS = [
    (r"\b(theorem|law|rule|paradox|principle|hypothesis|lemma)\b", "principle"),
    (r"\b(method|elimination|algorithm|analysis|regression|clustering|testing"
     r"|multiplication|addition|transposition|substitution|integration"
     r"|regularisation|regularization|boosting|bagging|stacking|augmentation)\b", "method"),
    (r"\b(framework|model|architecture|network|matrix of|playbook)\b", "framework"),
    (r"\b(accuracy|precision|recall|score|metric|variance|deviation|value"
     r"|determinant|auc|f1)\b", "metric"),
    (r"\b(diagram|table|tool|notation)\b", "tool"),
]


def guess_kind(name: str) -> str:
    low = name.lower()
    for rx, kind in KIND_HINTS:
        if re.search(rx, low):
            return kind
    return "definition"


def structural_links(extractions: list[dict], unit_index: list[dict],
                     topics: dict[str, str], sequential: bool = False) -> list[dict]:
    """The only edges a syllabus justifies without a model.

    `part_of` — when a Topic/Block heading is itself one of the extracted
    concepts ("Limits", "Derivatives", "Supervised Learning"), the concepts
    taught under it are its components. This is read off the document's own
    hierarchy, not inferred.

    `prerequisite` (opt-in, `sequential=True`) — chains the *umbrella* concept
    of consecutive lessons inside one course, on the assumption that a syllabus
    is ordered pedagogically. Marked `origin="sequence"` so it can be filtered
    out; it encodes calendar order, which is NOT the same as logical dependency.
    """
    import collections
    by_uid = {u["unit_id"]: u for u in unit_index}
    all_slugs = {c["slug"] for e in extractions for c in e["concepts"]}
    links, seen = [], set()

    def add(src, dst, typ, strength, why, origin):
        if src == dst or src not in all_slugs or dst not in all_slugs:
            return
        k = (src, dst, typ)
        if k in seen:
            return
        seen.add(k)
        links.append({"src": src, "dst": dst, "type": typ, "strength": strength,
                      "rationale": why, "origin": origin})

    # --- part_of from the document's Topic/Block hierarchy ------------------
    for e in extractions:
        topic_slug = topics.get(e["unit_id"])
        if not topic_slug:
            continue
        for c in e["concepts"]:
            add(c["slug"], topic_slug, "part_of", 0.7,
                "taught under this topic heading in the syllabus", "structure")

    # --- optional sequential chain, per course -----------------------------
    if sequential:
        per_course = collections.defaultdict(list)
        for e in extractions:
            u = by_uid[e["unit_id"]]
            per_course[u["course"]].append((u["module"], u["unit"], e))
        for course, rows in per_course.items():
            rows.sort(key=lambda r: (r[0], r[1]))
            prev = None
            for _m, _u, e in rows:
                if not e["concepts"]:
                    continue
                head = e["concepts"][0]["slug"]      # umbrella of this lesson
                if prev:
                    add(prev, head, "prerequisite", 0.4,
                        "the syllabus schedules this lesson after the previous one",
                        "sequence")
                prev = head
    return links


def run(files: list[SourceFile], text_dir: Path, out_path: Path,
        default_difficulty: int = 3) -> tuple[list[dict], list[dict]]:
    """Returns (extractions, unit_index) built purely from syllabus structure."""
    extractions, unit_index = [], []
    for f in files:
        p = text_dir / f.text_key
        if not p.exists():
            continue
        blocks = syllabus_extract(p.read_text(encoding="utf-8", errors="replace"),
                                  f.rel_path)
        for i, b in enumerate(blocks, 1):
            module = b["topic_ordinal"] if b.get("topic_ordinal") else (b["week"] or 0)
            uid = f"{f.course_code}-{b['lesson_kind'][:3].upper()}{b['lesson_number']:02d}"
            if any(u["unit_id"] == uid for u in unit_index):
                uid = f"{uid}-{i}"
            concepts = []
            for c in b["concepts"]:
                concepts.append({
                    "slug": c["slug"], "name": c["name"],
                    "kind": guess_kind(c["name"]),
                    "difficulty": default_difficulty,
                    "domain": f.course_code.lower(),
                    "definition": "",
                    "aliases": [],
                    "occurrences": c["occurrences"],
                })
            unit_index.append({"unit_id": uid, "course": f.course_code,
                               "module": module or 0, "unit": b["lesson_number"],
                               "title": b["title"] or f"{b['lesson_kind']} {b['lesson_number']}",
                               "n_files": 1, "chars": 0,
                               "files": [{"rel_path": f.rel_path,
                                          "asset_kind": f.asset_kind,
                                          "filename": f.filename}]})
            extractions.append({"unit_id": uid, "concepts": concepts, "links": []})
    out_path.write_text(json.dumps(extractions, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    return extractions, unit_index


def topic_map(files: list[SourceFile], text_dir: Path,
              extractions: list[dict], unit_index: list[dict]) -> dict[str, str]:
    """unit_id -> slug of its Topic/Block heading, when that heading is itself an
    extracted concept."""
    from .syllabus import extract as sx, _slug
    all_slugs = {c["slug"] for e in extractions for c in e["concepts"]}
    out = {}
    for f in files:
        p = text_dir / f.text_key
        if not p.exists():
            continue
        blocks = sx(p.read_text(encoding="utf-8", errors="replace"), f.rel_path)
        for i, b in enumerate(blocks, 1):
            if not b.get("topic"):
                continue
            uid = f"{f.course_code}-{b['lesson_kind'][:3].upper()}{b['lesson_number']:02d}"
            cand = _slug(re.sub(r"^\d+\.?\s*", "", b["topic"]))
            for s2 in (cand, cand.split("-and-")[0]):
                if s2 in all_slugs:
                    out[uid] = s2
                    break
    return out
