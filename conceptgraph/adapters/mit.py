"""MIT Sloan / GetSmarter dumps.

Filenames encode the whole structure, e.g.
  NUR/nur3/MIT NUR M3U2 Video Set Video 3 Part 2 Transcript.pdf
  -> course NUR, module 3, unit 2, video_transcript, video 3, part 2
"""
from __future__ import annotations
import os
import re

from .base import SourceAdapter, register

UNIT_RE = re.compile(r"\b(?:M\s?(\d+)|OM)\s?U\s?(\d+)\b", re.I)
MODULE_ONLY_RE = re.compile(r"\bModule\s+(\d+)\b", re.I)
VIDEO_RE = re.compile(r"\bVideo\s+(\d+)\b", re.I)
PODCAST_RE = re.compile(r"\bPodcast\s+(\d+)\b", re.I)
PART_RE = re.compile(r"\bPart\s+(\d+)\b", re.I)

# ordered: first match wins
KIND_RULES = [
    (r"podcast.*transcript", "podcast_transcript"),
    (r"(infographic|flowchart).*transcript", "infographic_transcript"),
    (r"transcript", "video_transcript"),
    (r"handbook", "handbook"),
    (r"casebook", "casebook"),
    (r"\bnotes\b", "notes"),
    (r"\blesson\b", "lesson"),
    (r"assignment", "assignment"),
    (r"\bquiz\b|\bassessment\b", "quiz"),
    (r"resource list|glossary|navigation guide|reading", "reading"),
    (r"\.xlsx?$", "spreadsheet"),
]

COURSE_TITLES = {
    "AGAI": "Applied Generative AI for Digital Transformation",
    "AI": "Artificial Intelligence: Implications for Business Strategy",
    "DBS": "Digital Business Strategy",
    "NUR": "Neuroscience for Business",
    "SMM": "Strategic Social Media Marketing",
}


@register
class MITAdapter(SourceAdapter):
    name = "mit"

    def course_title(self, code: str) -> str:
        return COURSE_TITLES.get(code, code)

    def parse(self, rel_path: str) -> dict:
        parts = rel_path.split("/")
        course = parts[0]
        stem = os.path.splitext(parts[-1])[0]

        m = UNIT_RE.search(stem)
        if m:
            module = int(m.group(1)) if m.group(1) else 0
            unit = int(m.group(2))
        else:
            mo = MODULE_ONLY_RE.search(stem)
            if mo:
                module, unit = int(mo.group(1)), 0
            else:
                # module-level supplementary reading: infer from folder suffix (dbs4 -> 4)
                fm = re.search(r"(\d+)$", parts[1]) if len(parts) > 2 else None
                module, unit = (int(fm.group(1)) if fm else 0), 0

        low = parts[-1].lower()
        kind = next((k for pat, k in KIND_RULES if re.search(pat, low)), "reading")
        v = VIDEO_RE.search(stem) or PODCAST_RE.search(stem)
        p = PART_RE.search(stem)
        return {
            "course_code": course,
            "course_title": self.course_title(course),
            "module_ordinal": module,
            "unit_ordinal": unit,
            "asset_kind": kind,
            "asset_ordinal": int(v.group(1)) if v else None,
            "part_ordinal": int(p.group(1)) if p else None,
        }
