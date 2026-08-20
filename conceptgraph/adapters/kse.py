"""KSE dumps — template.

Replace the regexes with whatever KSE actually uses. The pipeline only needs
parse() to return the five structural fields; nothing downstream changes.

Assumed layout (edit to match reality):
    <course>/<module>/<unit>/<asset>.pdf
    kse-ml/module-02/unit-03/lecture-01-transcript.pdf
"""
from __future__ import annotations
import os
import re

from .base import SourceAdapter, register

MODULE_RE = re.compile(r"(?:module|модуль)[-_ ]?(\d+)", re.I)
UNIT_RE = re.compile(r"(?:unit|lesson|тема|заняття)[-_ ]?(\d+)", re.I)
ORDINAL_RE = re.compile(r"(?:lecture|video|part|частина)[-_ ]?(\d+)", re.I)

KIND_RULES = [
    (r"transcript|транскрипт", "video_transcript"),
    (r"syllabus|силабус|handbook", "handbook"),
    (r"notes|конспект", "notes"),
    (r"lecture|лекц", "lesson"),
    (r"case|кейс", "casebook"),
    (r"test|quiz|тест", "quiz"),
    (r"assignment|завдання", "assignment"),
    (r"\.xlsx?$", "spreadsheet"),
]


@register
class KSEAdapter(SourceAdapter):
    name = "kse"

    def parse(self, rel_path: str) -> dict:
        parts = rel_path.split("/")
        course = parts[0]
        hay = rel_path
        stem = os.path.splitext(parts[-1])[0]

        mm, um = MODULE_RE.search(hay), UNIT_RE.search(hay)
        o = ORDINAL_RE.search(stem)
        low = parts[-1].lower()
        return {
            "course_code": course,
            "course_title": course,
            "module_ordinal": int(mm.group(1)) if mm else 0,
            "unit_ordinal": int(um.group(1)) if um else 0,
            "asset_kind": next((k for pat, k in KIND_RULES if re.search(pat, low)), "reading"),
            "asset_ordinal": int(o.group(1)) if o else None,
            "part_ordinal": None,
        }
