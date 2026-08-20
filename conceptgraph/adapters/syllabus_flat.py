"""Flat syllabus dump: one syllabus PDF per course directly under source_root,
e.g. `sources/Syllabus_Calculus.pdf`. No subfolders, so course_code must come
from the filename itself rather than the path (unlike mit.py / kse.py, which
read parts[0] of the relative path).
"""
from __future__ import annotations
import os
import re

from .base import SourceAdapter, register

COURSE_CODES = {
    "calculus": "CALC",
    "linear_algebra": "LINALG",
    "machine_learning": "ML",
    "probability_essentials": "PROB",
}


@register
class SyllabusFlatAdapter(SourceAdapter):
    name = "syllabus"

    def parse(self, rel_path: str) -> dict:
        stem = os.path.splitext(os.path.basename(rel_path))[0]
        name = re.sub(r"^syllabus[_-]?", "", stem, flags=re.I)
        key = re.sub(r"[^a-z]+", "_", name.lower()).strip("_")
        code = COURSE_CODES.get(key) or re.sub(r"[^A-Z]", "", name.upper())[:8] or key.upper()
        title = name.replace("_", " ").strip() or code
        return {
            "course_code": code,
            "course_title": title,
            "module_ordinal": 0,
            "unit_ordinal": 0,
            "asset_kind": "handbook",
        }
