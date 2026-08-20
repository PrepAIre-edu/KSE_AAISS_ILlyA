# -*- coding: utf-8 -*-
"""Deterministic concept extraction from syllabi — no LLM, no API key, no cost.

Why this works at all: a syllabus is not prose that has to be understood, it is
a *list of topic names* already written out. In every KSE syllabus the concept
names appear verbatim — in the "Content" cell of the schedule table, in a
module's bullet list of topics, or after "Lecture N:". Extraction is therefore
a parsing problem, not a comprehension problem.

What this CANNOT do: write definitions (the syllabus has none), decide the kind
of a concept, or infer link types. Those still need a model — but a much smaller
one, because it is given the names and only has to describe and connect them.
"""
from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass, field

# ---- lesson anchors: the granularity at which concepts get attributed --------
ANCHORS = [
    # (regex, kind, whether the trailing text on the same line is content)
    (r"^\s*Problem\s+Solving\s+Session\s+(\d+)\.?\s*(.*)$", "session", True),
    (r"^\s*Preparation\s+lecture\s+materials\s+(\d+)\.?\s*(.*)$", "materials", True),
    (r"^\s*Practice\s+session\s+(\d+)\.?\s*(.*)$", "practice", True),
    (r"^\s*Lecture\s+(\d+)\s*:?\s*(.*)$", "lecture", True),
    (r"^\s*(\d+)\s*\(Week[^)]*\)\s*$", "module", False),
]
TOPIC_RE = re.compile(r"^\s*(?:Topic|Block)\s+(\d+)\.?\s*(.*)$", re.I)
WEEK_RE = re.compile(r"^\s*Week\s+(\d+)\b", re.I)
BULLET_RE = re.compile(r"^\s*[●•▪◆▲*·-]\s*(.+)$")

# ---- fragments that are administrative, not knowledge -----------------------
NOISE = re.compile(
    r"^(?:"
    r"test\s*\d*|topic\s+test.*|quiz\s*\d*|homework\s*\d*|exam"
    r"|preparation\s+for\s+test.*|test\s+preparation.*|revision\s+session.*"
    r"|total\s+recall.*|course\s+wrap-?up.*|introduction\s+lecture.*"
    r"|introductory\s+lecture.*|answering\s+questions.*"
    r"|khanacademy.*|project|practice\s+session\s*\d*|problem\s+solving"
    r"|\d+h?\s*(?:problem\s+solving|class\s+hours?)?|activity|content|module|topics"
    r"|briefly|recap|examples?|properties|their\s+properties|applications?"
    r"|some\s+applications.*|and\s+\(?possibly\)?.*"
    r")\.?\s*$", re.I)

# splitters, in order: sentence boundary, then list separators. The PUA bullet
# glyphs (Wingdings-style, e.g. ) show up mid-line where a table cell
# boundary collapsed during PDF text extraction, not just at line starts like
# the BULLET_RE ones do — so they must split fragments apart, not just anchor them.
SPLIT_RE = re.compile(r"(?:\.\s+|\.$|;|,| and | or |/(?=[A-Z])|\n|\s*[●•▪◆▲]\s*)")

MIN_LEN, MAX_LEN = 3, 58

# schedule shorthand ("6 class hours (problem solving: 6h)", "2h problem solving
# 2h problem solving") comes in too many word orders for NOISE's fixed patterns
# to catch; instead, flag a fragment as admin noise if every non-numeric token
# in it is drawn from this small vocabulary.
_ADMIN_TOKEN_RE = re.compile(
    r"^(?:h|hrs?|hours?|class|problem|solving|session|sessions|week|weeks|test|tests"
    r"|homework|quiz|quizzes|exam|exams|preparation|revision|practice|lecture|lectures"
    r"|materials|block|blocks|before|after|for|content)$", re.I)


def _is_schedule_admin(f: str) -> bool:
    stripped = re.sub(r"[()./:,]", " ", f)
    stripped = re.sub(r"\d+\.?\d*", " ", stripped)
    words = stripped.split()
    return bool(words) and all(_ADMIN_TOKEN_RE.match(w) for w in words)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    for a, b in (("’", "'"), ("–", "-"), ("—", "-"), ("​", ""), ("­", ""),
                 ("\xa0", " ")):
        s = s.replace(a, b)
    return s


def _slug(s: str) -> str:
    s = re.sub(r"\([^)]*\)", " ", s.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return re.sub(r"-{2,}", "-", s)


def _clean_fragment(f: str) -> str:
    f = _norm(f).strip(" .;,:()")
    f = re.sub(r"\s+", " ", f)
    f = re.sub(r"^(?:the|a|an)\s+", "", f, flags=re.I)
    # strip a leading schedule-table column label that pdftotext merged onto
    # the same line as the actual cell content ("Content  Preparation for...")
    f = re.sub(r"^(?:content|topic|materials?)\s{2,}", "", f, flags=re.I)
    return f.strip()


@dataclass
class Lesson:
    kind: str
    number: int
    title: str
    week: int | None
    topic: str | None
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join([self.title] + self.lines).strip()


def split_lessons(text: str) -> list[Lesson]:
    """Cut a syllabus into lesson-sized blocks using the anchors above."""
    lessons: list[Lesson] = []
    week = topic = None
    cur: Lesson | None = None
    for raw in _norm(text).splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if (m := WEEK_RE.match(line)):
            week = int(m.group(1))
            continue
        if (m := TOPIC_RE.match(line)):
            topic = m.group(2).strip() or m.group(0).strip()
            # a Topic/Block heading also names knowledge; keep it as content
            if cur is None:
                cur = Lesson("topic", 0, topic, week, topic)
                lessons.append(cur)
            continue
        hit = None
        for rx, kind, inline in ANCHORS:
            if (m := re.match(rx, line, re.I)):
                hit = (kind, int(m.group(1)),
                       (m.group(2).strip() if inline and m.lastindex >= 2 else ""))
                break
        if hit:
            cur = Lesson(hit[0], hit[1], hit[2], week, topic)
            lessons.append(cur)
            continue
        if cur is not None:
            cur.lines.append(line)
    return lessons


def concept_names(lesson: Lesson) -> list[str]:
    """Split a lesson block into candidate concept names."""
    out, seen = [], set()
    body = lesson.text
    # Bullets are already one-concept-per-line. But the non-bullet lines around
    # them are the section heading, which names the umbrella concept
    # ("Deep Learning" above its bullet list) — keep both or the umbrella is lost.
    bullets, heads, seen_bullet = [], [], False
    for ln in body.splitlines():
        if (m := BULLET_RE.match(ln)):
            bullets.append(m.group(1))
            seen_bullet = True
        elif ln.strip() and not seen_bullet and len(ln.strip()) <= 45:
            # only the short line(s) directly above the bullet run: that is the
            # section heading. Anything after the bullets, or long, is prose.
            heads.append(ln.strip())
    if bullets:
        chunks = heads[-2:] + bullets      # heading is the last line before the list
    else:
        chunks = SPLIT_RE.split(body)
    for ch in chunks:
        for piece in (SPLIT_RE.split(ch) if bullets else [ch]):
            f = _clean_fragment(piece)
            if not (MIN_LEN <= len(f) <= MAX_LEN):
                continue
            if NOISE.match(f) or not re.search(r"[A-Za-z]{3}", f):
                continue
            if _is_schedule_admin(f):
                continue
            if f.lower().startswith(("using ", "usage of ", "building ", "finding ",
                                     "preparation", "operations with")):
                f = re.sub(r"^(?:using|usage of|building|finding|preparation for|operations with)\s+",
                           "", f, flags=re.I).strip()
                if not (MIN_LEN <= len(f) <= MAX_LEN):
                    continue
            k = _slug(f)
            if k and k not in seen:
                seen.add(k)
                out.append(f)
    return out


def extract(text: str, rel_path: str) -> list[dict]:
    """Return raw concept candidates with verbatim evidence, grouped by lesson."""
    units = []
    for i, les in enumerate(split_lessons(text), 1):
        names = concept_names(les)
        if not names:
            continue
        quote = les.text.split("\n")[0][:300] or les.title
        tord = None
        if les.topic:
            m = re.search(r"(\d+)", les.topic)
            tord = int(m.group(1)) if m else None
        units.append({
            "lesson_kind": les.kind, "lesson_number": les.number,
            "week": les.week, "topic": les.topic, "topic_ordinal": tord,
            "title": les.title,
            "concepts": [{"slug": _slug(n), "name": n,
                          "occurrences": [{"rel_path": rel_path, "role": "introduced",
                                           "quote": quote, "confidence": 0.6}]}
                         for n in names],
        })
    return units
