"""conceptgraph — turn a folder of course material into a concept graph.

    export ANTHROPIC_API_KEY=...
    python -m conceptgraph run  ~/Downloads/MIT --out ./build --adapter mit
    python -m conceptgraph plan ~/Downloads/MIT --out ./build      # no API calls
    python -m conceptgraph run  ~/Downloads/MIT --out ./build --force extract
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .adapters import registered
from .config import Settings
from .pipeline import STAGES, run as run_pipeline


def _settings(a) -> Settings:
    kw = {}
    if getattr(a, "courses", None):
        kw["courses"] = tuple(c.strip() for c in a.courses.split(",") if c.strip())
    for f in ("backend", "base_url", "api_key_env", "local_num_ctx"):
        v = getattr(a, f, None)
        if v is not None and not (f == "backend" and v == "free"):
            kw[f] = v
    if getattr(a, "raw_text", False):
        kw["pdf_layout"] = False
    if a.cmd == "free" and not getattr(a, "layout_text", False):
        kw["pdf_layout"] = False          # syllabi are tables; raw mode wins
    for f in ("extract_model", "consolidate_model", "max_workers",
              "batch_target_chars", "min_quote_verbatim_rate"):
        v = getattr(a, f, None)
        if v is not None:
            kw[f] = v
    if getattr(a, "no_gate", False):
        kw["fail_on_gate"] = False
    return Settings(source_root=Path(a.source).expanduser(),
                    work_dir=Path(a.out).expanduser(), adapter=a.adapter, **kw)


def main(argv=None) -> int:
    p = argparse.ArgumentParser("conceptgraph", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("source", help="folder containing the course dump")
        sp.add_argument("--out", required=True, help="work directory for artifacts")
        sp.add_argument("--adapter", default="mit", choices=registered())
        sp.add_argument("--courses", help="comma-separated course codes to process, "
                                          "e.g. DBS,AI (default: all)")
        sp.add_argument("--backend", default="anthropic",
                        choices=["anthropic", "ollama", "openai", "free"],
                        help="'free' = deterministic syllabus parsing, no API calls at all")
        sp.add_argument("--base-url", help="for --backend openai")
        sp.add_argument("--num-ctx", dest="local_num_ctx", type=int,
                        help="context window of the local model (default 32768); "
                             "batch size is derived from it")
        sp.add_argument("--raw-text", action="store_true",
                        help="extract PDFs without -layout (right for tables/syllabi)")
        sp.add_argument("--layout-text", action="store_true",
                        help="force -layout even on the free/syllabus path")
        sp.add_argument("--api-key-env", default="LLM_API_KEY",
                        help="env var holding the key for --backend openai")
        sp.add_argument("--extract-model", dest="extract_model")
        sp.add_argument("--consolidate-model", dest="consolidate_model")
        sp.add_argument("--max-workers", dest="max_workers", type=int)
        sp.add_argument("--batch-chars", dest="batch_target_chars", type=int)
        return sp

    dc = sub.add_parser("doctor", help="check the backend/model before a real run")
    dc.add_argument("--backend", default="ollama",
                    choices=["anthropic", "ollama", "openai"])
    dc.add_argument("--extract-model", dest="extract_model",
                    default="qwen2.5:14b-instruct")
    dc.add_argument("--consolidate-model", dest="consolidate_model")
    dc.add_argument("--base-url", help="Ollama host, or the OpenAI-compatible base URL")
    dc.add_argument("--api-key-env", default="LLM_API_KEY")
    dc.add_argument("--num-ctx", dest="local_num_ctx", type=int, default=32768)

    mb = sub.add_parser("moodle", help="unpack Moodle .mbz backup(s) into a readable folder")
    mb.add_argument("source", help=".mbz file, or a folder containing .mbz files")
    mb.add_argument("--out", required=True, help="where to write the unpacked course")

    f = common(sub.add_parser("free", help="deterministic syllabus -> concepts, zero cost"))
    f.add_argument("--sequential-links", action="store_true",
                   help="also chain consecutive lessons as weak prerequisites "
                        "(origin='sequence'; encodes calendar order, not logic)")

    r = common(sub.add_parser("run", help="run the full pipeline (resumable)"))
    r.add_argument("--stop-after", choices=STAGES)
    r.add_argument("--force", action="append", default=[], choices=STAGES,
                   help="re-run this stage even if its artifact exists (repeatable)")
    r.add_argument("--no-gate", action="store_true", help="export even if the gate fails")
    r.add_argument("--min-quote-rate", dest="min_quote_verbatim_rate", type=float)

    common(sub.add_parser("plan", help="scan, extract text, bundle — no API calls"))

    v = sub.add_parser("report", help="print the last quality report")
    v.add_argument("--out", required=True)

    a = p.parse_args(argv)

    if a.cmd == "doctor":
        from .doctor import run as doctor_run
        import tempfile
        kw = {"backend": a.backend, "extract_model": a.extract_model,
              "local_num_ctx": a.local_num_ctx, "api_key_env": a.api_key_env}
        if a.base_url:
            kw["base_url"] = a.base_url
        if a.consolidate_model:
            kw["consolidate_model"] = a.consolidate_model
        tmp = Path(tempfile.mkdtemp(prefix="cg-doctor-"))
        return doctor_run(Settings(source_root=tmp, work_dir=tmp, **kw))

    if a.cmd == "moodle":
        from .moodle import unpack_all
        reps = unpack_all(Path(a.source).expanduser(), Path(a.out).expanduser())
        if not reps:
            print("no .mbz files found", file=sys.stderr)
            return 1
        for r in reps:
            print(r.summary())
        if all(r.notes and "cannot work" in " ".join(r.notes) for r in reps):
            print("\nNothing worth extracting concepts from. Re-export from Moodle "
                  "with activities and files included, or supply the syllabus.")
            return 2
        return 0

    if a.cmd == "report":
        f = Path(a.out).expanduser() / "quality_report.json"
        if not f.exists():
            print(f"no report at {f}", file=sys.stderr)
            return 1
        from .validate import summary
        rep = json.loads(f.read_text(encoding="utf-8"))
        print(summary(rep))
        if rep.get("units_out_of_range"):
            print("\n  units outside the concepts-per-unit range:")
            for u, n in rep["units_out_of_range"]:
                print(f"    {u:<14} {n}")
        if rep.get("quotes_unverified"):
            print(f"\n  {rep['quotes_unverified']} unverified quotes "
                  f"— see quality_report.json -> unverified_quotes")
        return 0

    s = _settings(a)
    if a.cmd == "free":
        from .pipeline import run_free
        run_free(s, sequential=a.sequential_links)
        return 0
    if a.cmd == "plan":
        run_pipeline(s, dry_run=True)
        print("\nplan only — no API calls made. `run` to continue.")
        return 0
    run_pipeline(s, stop_after=a.stop_after, force=set(a.force))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
