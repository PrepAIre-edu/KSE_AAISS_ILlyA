"""Stage orchestration. Every stage writes its artifact and is skipped if that
artifact already exists, so `run` is resumable and `--force` re-does a stage."""
from __future__ import annotations
import json
import time
from pathlib import Path

from . import bundle as bundle_mod
from . import consolidate, export, extract, graph as G, textract, validate
from .adapters import SourceFile, get_adapter
from .config import Settings
from .llm import LLM

STAGES = ["scan", "textract", "bundle", "extract", "consolidate", "graph", "validate", "export"]


def _make_llm(s: Settings):
    """Build the client the settings ask for. All backends expose .structured(),
    so extract.py and consolidate.py never learn which one they got."""
    if s.backend == "anthropic":
        return LLM(s)
    import os
    from . import llm_local
    if s.backend == "ollama":
        return llm_local.OllamaLLM(s, host=s.base_url or "http://localhost:11434")
    if s.backend == "openai":
        if not s.base_url:
            raise SystemExit("--backend openai requires --base-url")
        key = os.environ.get(s.api_key_env)
        if not key:
            raise SystemExit(f"--backend openai requires ${s.api_key_env} to be set")
        return llm_local.OpenAILLM(s, base_url=s.base_url, api_key=key)
    raise SystemExit(f"unknown backend {s.backend!r}")


def _load_files(s: Settings) -> list[SourceFile]:
    raw = json.loads(s.path("manifest.json").read_text(encoding="utf-8"))
    return [SourceFile(**{k: v for k, v in r.items()
                          if k in SourceFile.__dataclass_fields__}) for r in raw]


def run(s: Settings, *, stop_after: str | None = None, force: set[str] | None = None,
        dry_run: bool = False) -> dict:
    force = force or set()
    t0 = time.time()
    out: dict = {"settings": s.to_dict(), "stages": {}}

    def done(stage: str, **kw):
        out["stages"][stage] = kw
        print(f"[{stage}] " + ", ".join(f"{k}={v}" for k, v in kw.items()))

    # 1. scan ---------------------------------------------------------------
    mf = s.path("manifest.json")
    if "scan" in force or not mf.exists():
        files = get_adapter(s.adapter).scan(s.source_root)
        mf.write_text(json.dumps([f.to_dict() for f in files], ensure_ascii=False, indent=1),
                      encoding="utf-8")
    files = _load_files(s)
    if s.courses:
        keep = {c.upper() for c in s.courses}
        before = len(files)
        files = [f for f in files if f.course_code.upper() in keep]
        if not files:
            raise SystemExit(f"no files match --courses {sorted(keep)}")
        print(f"[filter] courses={sorted(keep)} kept {len(files)}/{before} files")
    done("scan", files=len(files), courses=len({f.course_code for f in files}),
         units=len({f.unit_id for f in files}))
    if stop_after == "scan":
        return out

    # 2. textract -----------------------------------------------------------
    text_dir = s.work_dir / "txt"
    rep = textract.extract_all(files, s.source_root, text_dir, s.min_file_chars,
                               layout=s.pdf_layout)
    done("textract", extracted=rep["ok"], cached=rep["cached"],
         failed=len(rep["failed"]), short=len(rep["skipped_short"]))
    if rep["failed"]:
        s.path("textract_failures.json").write_text(
            json.dumps(rep["failed"], indent=1), encoding="utf-8")
    if stop_after == "textract":
        return out

    # 3. bundle -------------------------------------------------------------
    bundle_dir = s.work_dir / "units"
    ui = s.path("unit_index.json")
    if "bundle" in force or not ui.exists():
        index = bundle_mod.build(files, text_dir, bundle_dir,
                                 skip_module_ordinals=s.skip_module_ordinals,
                                 min_file_chars=s.min_file_chars)
        ui.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    unit_index = json.loads(ui.read_text(encoding="utf-8"))
    batches = bundle_mod.make_batches(unit_index, s.batch_target_chars)
    s.path("batches.json").write_text(
        json.dumps([{**b, "units": [u["unit_id"] for u in b["units"]]} for b in batches],
                   indent=1), encoding="utf-8")
    done("bundle", units=len(unit_index),
         chars=sum(u["chars"] for u in unit_index), batches=len(batches))
    if dry_run or stop_after == "bundle":
        out["dry_run"] = True
        return out

    llm = _make_llm(s)
    print(f"[llm] backend={s.backend} extract={s.extract_model} "
          f"consolidate={s.consolidate_model} batch_chars={s.batch_target_chars:,}")

    # 4. extract (MAP) ------------------------------------------------------
    raw_dir = s.work_dir / "concepts_raw"
    if "extract" in force:
        for p in raw_dir.glob("b*.json"):
            p.unlink()
    extractions = extract.run(s, llm, batches, bundle_dir, raw_dir)
    s.path("extractions.json").write_text(
        json.dumps(extractions, ensure_ascii=False, indent=1), encoding="utf-8")
    done("extract", units=len(extractions),
         concepts=sum(len(u["concepts"]) for u in extractions),
         links=sum(len(u.get("links", [])) for u in extractions))
    if stop_after == "extract":
        return out

    # 5. consolidate (REDUCE) ----------------------------------------------
    g = G.build(extractions, unit_index)
    cf = s.path("consolidation.json")
    if "consolidate" in force or not cf.exists():
        merges = consolidate.find_merges(s, llm, g)
        # bridges are proposed against the post-merge catalogue
        g2 = G.build(extractions, unit_index)
        G.apply_merges(g2, merges)
        probe = G.finalise(G.build(extractions, unit_index))
        bridges = consolidate.find_bridges(s, llm, g2, probe.links)
        cf.write_text(json.dumps({"merges": merges, "bridge_links": bridges},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    cons = json.loads(cf.read_text(encoding="utf-8"))
    done("consolidate", merge_groups=len(cons["merges"]),
         absorbed=sum(len(m["absorb"]) for m in cons["merges"]),
         bridges=len(cons["bridge_links"]))
    if stop_after == "consolidate":
        return out

    # 6. graph --------------------------------------------------------------
    g = G.build(extractions, unit_index)
    absorbed = G.apply_merges(g, cons["merges"])
    g = G.finalise(g, cons["bridge_links"])
    done("graph", concepts=len(g.concepts), absorbed=absorbed, links=len(g.links),
         occurrences=len(g.occurrences), components=len(G.components(g)))
    if stop_after == "graph":
        return out

    # 7. validate -----------------------------------------------------------
    report = validate.run(g, files, text_dir, unit_index, s)
    s.path("quality_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(validate.summary(report))
    out["quality"] = {k: v for k, v in report.items()
                      if k not in {"unverified_quotes", "graph_notes"}}
    if not report["gate_passed"] and s.fail_on_gate:
        out["failed"] = report["gate_failures"]
        raise SystemExit("quality gate failed: " + "; ".join(report["gate_failures"]))
    if stop_after == "validate":
        return out

    # 8. export -------------------------------------------------------------
    counts = export.write_all(g, files, unit_index, s.work_dir / "dataset")
    done("export", **counts)

    out["usage"] = {"calls": llm.usage.calls, "cached": llm.usage.cached,
                    "input_tokens": llm.usage.input_tokens,
                    "output_tokens": llm.usage.output_tokens,
                    "retries": llm.usage.retries, "usd": llm.usage.usd,
                    "by_stage": llm.usage.by_stage}
    print("\n" + llm.usage.report())
    out["seconds"] = round(time.time() - t0, 1)
    s.path("run_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    return out


def run_free(s: Settings, *, sequential: bool = False) -> dict:
    """Zero-cost path: scan -> textract -> deterministic syllabus parse -> graph
    -> validate -> export. No API key, no network, fully reproducible.

    Gets you concept names with verbatim evidence and the syllabus's own
    hierarchy. Does NOT get you definitions or reasoned links — see README.
    """
    from . import extract_free

    mf = s.path("manifest.json")
    if not mf.exists():
        files0 = get_adapter(s.adapter).scan(s.source_root)
        mf.write_text(json.dumps([f.to_dict() for f in files0], ensure_ascii=False, indent=1),
                      encoding="utf-8")
    files = _load_files(s)
    if s.courses:
        keep = {c.upper() for c in s.courses}
        files = [f for f in files if f.course_code.upper() in keep]
    print(f"[scan] files={len(files)}")

    text_dir = s.work_dir / "txt"
    rep = textract.extract_all(files, s.source_root, text_dir, s.min_file_chars,
                               layout=s.pdf_layout)
    print(f"[textract] extracted={rep['ok']} cached={rep['cached']} failed={len(rep['failed'])}")

    ex, ui = extract_free.run(files, text_dir, s.path("extractions.json"))
    s.path("unit_index.json").write_text(json.dumps(ui, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    tm = extract_free.topic_map(files, text_dir, ex, ui)
    links = extract_free.structural_links(ex, ui, tm, sequential=sequential)
    if ex:
        ex[0]["links"] = links
    print(f"[extract-free] units={len(ui)} concepts={sum(len(e['concepts']) for e in ex)} "
          f"structural_links={len(links)}")

    g = G.finalise(G.build(ex, ui))
    comps = G.components(g)
    print(f"[graph] concepts={len(g.concepts)} links={len(g.links)} components={len(comps)}")

    report = validate.run(g, files, text_dir, ui, s)
    s.path("quality_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
    print(validate.summary(report))
    counts = export.write_all(g, files, ui, s.work_dir / "dataset")
    print(f"[export] {counts}")
    if report["isolated_concepts"]:
        print(f"\nNOTE: {len(report['isolated_concepts'])} concepts have no links at all. "
              "A syllabus states topics, not dependencies — relations need a model.\n"
              "Next: `run --backend ollama` to add definitions and links for free.")
    return {"quality": report, "counts": counts}
