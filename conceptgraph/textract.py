"""Document -> plain text. Deterministic, cached on sha256, no LLM involved."""
from __future__ import annotations
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from .adapters import SourceFile

# Repeated furniture that survives pdftotext and pollutes every page.
BOILERPLATE = re.compile(
    r"^\s*(?:"
    r"©\s*20\d\d[^\n]{0,40}"
    r"|All Rights Reserved"
    r"|Page \d+ of \d+"
    r"|Tel:[^\n]*"
    r"|[a-z0-9.-]+\.(?:edu|com)\s*"
    r"|MODULE \d+ UNIT \d+"
    r"|(?:Video|Podcast) \d+ Transcript"
    r")\s*$",
    re.I | re.M,
)

_TRANS = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "‑": "-",
    "­": "", "​": "", "‌": "", "‍": "", "﻿": "",
    " ": " ",
})


def normalise(text: str) -> str:
    """Fold away the typographic variation that PDF extraction introduces."""
    return unicodedata.normalize("NFKC", text).translate(_TRANS)


def clean(text: str) -> str:
    t = normalise(text)
    t = BOILERPLATE.sub("", t)
    t = re.sub(r"-\s*\n\s*", "", t)          # de-hyphenate across line breaks
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def _pdftotext(src: Path, layout: bool = True) -> str:
    """`-layout` preserves column positions, which is right for prose and
    transcripts. It is WRONG for table-heavy documents such as syllabi: it
    interleaves adjacent table cells on the same output line, so a phrase that
    reads contiguously in the PDF does not exist contiguously in the text.
    Raw mode keeps each cell whole. Measured on the KSE syllabi: verbatim quote
    rate 75.7% with -layout versus 100% without."""
    if not shutil.which("pdftotext"):
        raise RuntimeError("pdftotext not found — install poppler-utils")
    cmd = ["pdftotext"] + (["-layout"] if layout else []) + ["-enc", "UTF-8", str(src), "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or b"").decode("utf-8", "replace")[:300])
    return r.stdout.decode("utf-8", "replace")


def _pymupdf(src: Path) -> str:
    """Fallback when poppler is absent — the usual case on Windows.

    PyMuPDF >= 1.24 renamed the module to `pymupdf` and keeps `fitz` only as a
    deprecated alias, so try the new name first.
    """
    try:
        import pymupdf                               # optional dependency
    except ImportError:
        import fitz as pymupdf                       # PyMuPDF < 1.24
    with pymupdf.open(src) as doc:
        return "\n".join(page.get_text() for page in doc)


def _xlsx(src: Path) -> str:
    """Sheet text without a spreadsheet dependency: shared strings + inline cells."""
    import zipfile
    from xml.etree import ElementTree as ET

    NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(src) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(f"{NS}t"))
                      for si in root.iter(f"{NS}si")]
        lines: list[str] = []
        for name in sorted(n for n in z.namelist()
                           if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")):
            root = ET.fromstring(z.read(name))
            for row in root.iter(f"{NS}row"):
                cells = []
                for c in row.iter(f"{NS}c"):
                    v = c.find(f"{NS}v")
                    txt = ""
                    if c.get("t") == "s" and v is not None and v.text is not None:
                        i = int(v.text)
                        txt = shared[i] if 0 <= i < len(shared) else ""
                    elif c.get("t") == "inlineStr":
                        txt = "".join(t.text or "" for t in c.iter(f"{NS}t"))
                    elif v is not None:
                        txt = v.text or ""
                    if txt.strip():
                        cells.append(txt.strip())
                if cells:
                    lines.append("\t".join(cells))
    return "\n".join(lines)


def extract_one(src: Path, layout: bool = True) -> str:
    suf = src.suffix.lower()
    if suf == ".pdf":
        try:
            return _pdftotext(src, layout=layout)
        except Exception:
            return _pymupdf(src)                     # raises if unavailable
    if suf in {".txt", ".md"}:
        return src.read_text(encoding="utf-8", errors="replace")
    if suf in {".xlsx", ".xlsm"}:
        return _xlsx(src)
    if suf == ".docx":
        import zipfile
        with zipfile.ZipFile(src) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        return re.sub(r"<[^>]+>", " ", xml.replace("</w:p>", "\n"))
    raise RuntimeError(f"no extractor for {suf}")


def extract_all(files: list[SourceFile], source_root: Path, text_dir: Path,
                min_chars: int = 200, layout: bool = True) -> dict:
    """Write one .txt per source file. Skips work already done (sha-keyed)."""
    text_dir.mkdir(parents=True, exist_ok=True)
    report = {"ok": 0, "cached": 0, "failed": [], "skipped_short": []}
    for f in files:
        out = text_dir / f.text_key
        if out.exists() and out.stat().st_size > 0:
            report["cached"] += 1
            continue
        try:
            raw = extract_one(source_root / f.rel_path, layout=layout)
        except Exception as e:                        # noqa: BLE001
            f.metadata["parse_error"] = str(e)[:300]
            report["failed"].append((f.rel_path, str(e)[:120]))
            continue
        if len(raw.strip()) < min_chars:
            report["skipped_short"].append(f.rel_path)
            continue
        out.write_text(raw, encoding="utf-8")
        report["ok"] += 1
    return report
