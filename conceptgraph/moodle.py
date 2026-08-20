# -*- coding: utf-8 -*-
"""Moodle .mbz backup -> a folder the normal pipeline can read.

A .mbz is a gzipped tar of XML. Two things inside matter:

  * activity/section XML — names, intros, summaries, page/book content,
    glossary entries, assignment briefs. This is where a course states what it
    teaches, when it states it at all.
  * files.xml + files/<xx>/<contenthash> — attached documents (PDF, DOCX...).
    Moodle stores them content-addressed, so the real filename lives only in
    files.xml. Restoring those names is what makes the attachments usable.

`unpack()` explodes both into a plain directory so `scan`/`textract`/`bundle`
work unchanged. The adapter contract (paths -> structure) stays intact.
"""
from __future__ import annotations
import html
import re
import shutil
import tarfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

# element names that hold human-readable course text
TEXT_TAGS = ("name", "intro", "summary", "content", "definition", "concept",
             "description", "activity", "page_after_submit", "message",
             "subject", "questiontext", "generalfeedback")

# activity types that can carry subject matter, roughly in order of value
CONTENTFUL = ("page", "book", "lesson", "resource", "folder", "assign", "quiz",
              "glossary", "label", "workshop", "wiki", "forum", "scorm", "url")

DOC_SUFFIXES = {".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
                ".txt", ".md", ".rtf", ".odt", ".csv"}


def _clean(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</li>|</div>|</h[1-6]>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _first(root, *tags) -> str:
    for t in tags:
        e = root.find(f".//{t}")
        if e is not None and e.text and e.text.strip():
            return _clean(e.text)
    return ""


@dataclass
class Report:
    course: str = ""
    sections: int = 0
    activities: int = 0
    by_type: dict = field(default_factory=dict)
    text_chars: int = 0
    written_txt: int = 0
    restored_docs: int = 0
    skipped_media: int = 0
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        L = [f"course: {self.course}",
             f"sections: {self.sections} | activities: {self.activities} {self.by_type}",
             f"text extracted: {self.text_chars:,} chars into {self.written_txt} file(s)",
             f"attachments restored: {self.restored_docs} document(s), "
             f"{self.skipped_media} media file(s) skipped"]
        return "\n".join("  " + x for x in L) + \
               ("\n" + "\n".join("  ! " + n for n in self.notes) if self.notes else "")


def unpack(mbz: Path, out_dir: Path) -> Report:
    """Extract one .mbz into out_dir/<course_slug>/... Returns what was found."""
    mbz, out_dir = Path(mbz), Path(out_dir)
    work = out_dir / ".mbz_raw" / mbz.stem[:60]
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    with tarfile.open(mbz, "r:*") as t:
        # guard against path traversal in untrusted archives
        members = [m for m in t.getmembers()
                   if not (m.name.startswith("/") or ".." in Path(m.name).parts)]
        t.extractall(work, members=members)

    rep = Report()

    # ---- course identity ---------------------------------------------------
    cx = work / "course" / "course.xml"
    full = short = ""
    if cx.exists():
        r = ET.parse(cx).getroot()
        full = _first(r, "fullname")
        short = _first(r, "shortname")
    rep.course = full or short or mbz.stem
    slug = re.sub(r"[^A-Za-z0-9]+", "_", (short or full or "course")).strip("_")[:40] or "course"
    root = out_dir / slug
    root.mkdir(parents=True, exist_ok=True)

    # ---- section titles ---------------------------------------------------
    sections: dict[str, tuple[int, str, str]] = {}
    for sd in sorted((work / "sections").glob("section_*")):
        sx = sd / "section.xml"
        if not sx.exists():
            continue
        r = ET.parse(sx).getroot()
        num = _first(r, "number") or "0"
        sections[r.get("id") or sd.name.split("_")[-1]] = (
            int(num) if num.isdigit() else 0, _first(r, "name"), _first(r, "summary"))
    rep.sections = len(sections)

    # ---- activities -------------------------------------------------------
    by_type: dict[str, int] = {}
    for ad in sorted((work / "activities").glob("*_*")):
        kind = ad.name.rsplit("_", 1)[0]
        by_type[kind] = by_type.get(kind, 0) + 1
        rep.activities += 1

        secnum, secname = 0, ""
        mx = ad / "module.xml"
        if mx.exists():
            mr = ET.parse(mx).getroot()
            sn = _first(mr, "sectionnumber")
            secnum = int(sn) if sn.isdigit() else 0
            sid = _first(mr, "sectionid")
            if sid in sections:
                secnum, secname = sections[sid][0], sections[sid][1]

        parts: list[str] = []
        for xf in sorted(ad.glob("*.xml")):
            if xf.name in {"grades.xml", "grade_history.xml", "roles.xml",
                           "filters.xml", "inforef.xml", "calendar.xml",
                           "completion.xml", "comments.xml"}:
                continue
            try:
                r = ET.parse(xf).getroot()
            except ET.ParseError:
                continue
            for e in r.iter():
                if e.tag in TEXT_TAGS and e.text:
                    t = _clean(e.text)
                    if len(t) > 2:
                        parts.append(f"[{e.tag}] {t}")
        body = "\n".join(dict.fromkeys(parts))          # dedupe, keep order
        if len(body) < 20:
            continue
        sec_dir = root / f"section_{secnum:02d}"
        sec_dir.mkdir(parents=True, exist_ok=True)
        header = (f"# {kind} activity from Moodle backup\n"
                  f"# course: {rep.course}\n"
                  f"# section {secnum}: {secname}\n\n")
        (sec_dir / f"{ad.name}.txt").write_text(header + body, encoding="utf-8")
        rep.written_txt += 1
        rep.text_chars += len(body)
    rep.by_type = by_type

    # section summaries are course content too
    for sid, (num, name, summ) in sorted(sections.items(), key=lambda x: x[1][0]):
        if len(summ) < 20:
            continue
        d = root / f"section_{num:02d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "section_summary.txt").write_text(
            f"# section {num}: {name}\n\n{summ}", encoding="utf-8")
        rep.written_txt += 1
        rep.text_chars += len(summ)

    # ---- restore attachments under their real names -----------------------
    fx = work / "files.xml"
    if fx.exists():
        r = ET.parse(fx).getroot()
        for f in r.findall("file"):
            g = lambda t: ((f.find(t).text or "") if f.find(t) is not None else "")
            fn, chash = g("filename"), g("contenthash")
            if not fn or fn in (".", "") or not chash:
                continue
            blob = work / "files" / chash[:2] / chash
            if not blob.exists():
                continue
            if Path(fn).suffix.lower() not in DOC_SUFFIXES:
                rep.skipped_media += 1
                continue
            dest = root / "attachments"
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / fn
            i = 1
            while target.exists():
                target = dest / f"{Path(fn).stem}_{i}{Path(fn).suffix}"
                i += 1
            shutil.copy(blob, target)
            rep.restored_docs += 1

    # The decisive signal is not how much text there is, but whether any
    # activity type that can *hold* subject matter is present at all. A course
    # made only of url/forum/feedback links its content out to somewhere else.
    SUBSTANTIVE = {"page", "book", "lesson", "resource", "folder", "assign",
                   "quiz", "workshop", "wiki", "scorm"}
    have = SUBSTANTIVE & set(by_type)
    if not have and rep.restored_docs == 0:
        outbound = sum(v for k, v in by_type.items() if k in {"url", "lti"})
        rep.notes.append(
            "no content-bearing activity (page/book/resource/assign/quiz) and no "
            "attached documents. This backup describes the course shell only"
            + (f"; the {outbound} url activities point the content elsewhere"
               if outbound else "") + ".")
        rep.notes.append(
            "Concept extraction cannot work on this. Re-export from Moodle with "
            "\"Include activities and resources\" and \"Include files\" enabled, "
            "or supply the syllabus document instead.")
    elif rep.text_chars < 3000 and rep.restored_docs == 0:
        rep.notes.append(
            f"only {rep.text_chars:,} characters of text and no documents — expect "
            "very few concepts.")
    return rep


def unpack_all(src: Path, out_dir: Path) -> list[Report]:
    src = Path(src)
    files = [src] if src.is_file() else sorted(src.glob("*.mbz"))
    return [unpack(f, out_dir) for f in files]
