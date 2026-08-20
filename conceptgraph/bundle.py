"""Group extracted text into one cleaned bundle per unit.

The bundle is what the map stage reads. Each file inside is delimited by a
`### FILE: <rel_path>` marker, which is how the model knows which rel_path to
attribute a quote to.
"""
from __future__ import annotations
import json
from collections import OrderedDict
from pathlib import Path

from .adapters import SourceFile
from .textract import clean

FILE_MARKER = "### FILE:"


def build(files: list[SourceFile], text_dir: Path, bundle_dir: Path, *,
          skip_module_ordinals: tuple[int, ...] = (0,), min_file_chars: int = 200) -> list[dict]:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    # Module 0 means "orientation" for MIT dumps, so it is skipped by default.
    # But an adapter that cannot infer a module number also returns 0 — a folder
    # of standalone syllabi is the case — and skipping would then discard
    # everything. Only skip when something would actually remain.
    kept = [f for f in files if f.module_ordinal not in skip_module_ordinals]
    if not kept and files:
        print("[bundle] every file is in a skipped module — keeping them all "
              "(the adapter probably could not infer module numbers)")
        kept = files

    units: OrderedDict[str, list[SourceFile]] = OrderedDict()
    for f in kept:
        units.setdefault(f.unit_id, []).append(f)

    index: list[dict] = []
    for unit_id, group in units.items():
        parts, srcs, total = [], [], 0
        for f in group:
            p = text_dir / f.text_key
            if not p.exists():
                continue
            body = clean(p.read_text(encoding="utf-8", errors="replace"))
            if len(body) < min_file_chars:
                continue
            parts.append(f"\n\n{FILE_MARKER} {f.rel_path}  [{f.asset_kind}]\n\n{body}")
            srcs.append({"rel_path": f.rel_path, "asset_kind": f.asset_kind,
                         "filename": f.filename})
            total += len(body)
        if not parts:
            continue
        g = group[0]
        header = (f"# UNIT {unit_id} — course {g.course_code} "
                  f"({g.course_title}), module {g.module_ordinal}, unit {g.unit_ordinal}\n")
        (bundle_dir / f"{unit_id}.txt").write_text(header + "".join(parts), encoding="utf-8")
        index.append({"unit_id": unit_id, "course": g.course_code,
                      "module": g.module_ordinal, "unit": g.unit_ordinal,
                      "n_files": len(srcs), "chars": total, "files": srcs})
    return index


def make_batches(index: list[dict], target_chars: int) -> list[dict]:
    """Greedy longest-first bin packing so every LLM call sees a similar load.

    Units larger than target_chars get a batch to themselves rather than being
    split, because splitting a unit would break the 5-10 concepts-per-unit rule.
    """
    order = sorted(index, key=lambda u: -u["chars"])
    n = max(1, sum(u["chars"] for u in index) // max(1, target_chars) + 1)
    bins: list[list[dict]] = [[] for _ in range(n)]
    sizes = [0] * n
    for u in order:
        i = sizes.index(min(sizes))
        bins[i].append(u)
        sizes[i] += u["chars"]
    return [{"batch": f"b{i:02d}", "units": sorted(b, key=lambda u: u["unit_id"]),
             "chars": s}
            for i, (b, s) in enumerate(zip(bins, sizes), 1) if b]
