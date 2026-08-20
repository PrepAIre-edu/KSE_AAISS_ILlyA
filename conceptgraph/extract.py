"""MAP stage: one LLM call per batch of units -> concepts + intra-batch links."""
from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import Settings
from .llm import LLM
from .prompts import EXTRACT_SYSTEM, EXTRACT_USER
from .schemas import BatchExtraction


def _system(s: Settings) -> str:
    return EXTRACT_SYSTEM.format(
        min_c=s.min_concepts_per_unit, max_c=s.max_concepts_per_unit,
        hard_max=s.hard_max_concepts_per_unit,
        min_q=s.min_quote_chars, max_q=s.max_quote_chars,
        links_per=s.target_links_per_concept)


def _prompt(batch: dict, bundle_dir: Path) -> str:
    blobs = []
    for u in batch["units"]:
        text = (bundle_dir / f"{u['unit_id']}.txt").read_text(encoding="utf-8")
        blobs.append(f"===== UNIT {u['unit_id']} =====\n{text}")
    return EXTRACT_USER.format(n=len(batch["units"]), bundles="\n\n".join(blobs))


def run(settings: Settings, llm: LLM, batches: list[dict], bundle_dir: Path,
        out_dir: Path) -> list[dict]:
    """Returns the flat list of UnitExtraction dicts across all batches."""
    out_dir.mkdir(parents=True, exist_ok=True)
    system = _system(settings)
    results: dict[str, list[dict]] = {}

    def one(batch: dict) -> tuple[str, list[dict]]:
        dest = out_dir / f"{batch['batch']}.json"
        if dest.exists():
            return batch["batch"], json.loads(dest.read_text(encoding="utf-8"))["units"]
        got = llm.structured(
            stage="extract", model=settings.extract_model, system=system,
            prompt=_prompt(batch, bundle_dir), out_model=BatchExtraction)
        units = [u.model_dump() for u in got.units]

        # the model occasionally returns a unit_id it invented; repair by position
        wanted = [u["unit_id"] for u in batch["units"]]
        if {u["unit_id"] for u in units} - set(wanted) and len(units) == len(wanted):
            for u, w in zip(units, wanted):
                u["unit_id"] = w
        dest.write_text(json.dumps({"batch": batch["batch"], "units": units},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        return batch["batch"], units

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
        futs = {pool.submit(one, b): b for b in batches}
        for f in as_completed(futs):
            b = futs[f]
            try:
                name, units = f.result()
                results[name] = units
                print(f"  extract {name}: {len(units)} units, "
                      f"{sum(len(u['concepts']) for u in units)} concepts")
            except Exception as e:                                    # noqa: BLE001
                failures.append(f"{b['batch']}: {e}")
                print(f"  extract {b['batch']}: FAILED — {e}")

    out = [u for name in sorted(results) for u in results[name]]
    # Fail loudly here. Letting an empty extraction through means the quality
    # gate reports "0% verbatim quotes", which looks like a data problem when it
    # is really an unreachable backend or a wrong model name.
    if batches and not out:
        raise SystemExit(
            f"extraction produced nothing — all {len(batches)} batch(es) failed.\n  "
            + "\n  ".join(failures[:5]))
    if failures and len(failures) > len(batches) // 2:
        raise SystemExit(
            f"extraction failed for {len(failures)}/{len(batches)} batches; "
            "refusing to build a graph from a partial run.\n  "
            + "\n  ".join(failures[:5]))
    return out
