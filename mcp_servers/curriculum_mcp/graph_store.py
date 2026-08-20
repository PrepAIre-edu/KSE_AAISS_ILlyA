"""Loads the conceptgraph dataset + curated course metadata into memory once,
at server startup, and exposes the domain operations every tool needs.

Two data layers, kept deliberately separate:
  - `concept_graph.json`  — extracted by conceptgraph (see repo root), real
    concepts/links per course. No concept or link crosses a course boundary
    in the current dataset (see docs/MCP_ASSIGNMENT_PLAN.md for why).
  - `course_metadata.json` — hand-transcribed ECTS + prerequisite text from
    each syllabus's own "ECTS credits" / "Prerequisites" section. Course-level
    facts a syllabus states about itself, not something conceptgraph extracts.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = _REPO_ROOT / "output" / "dataset"
METADATA_PATH = Path(__file__).resolve().parent / "course_metadata.json"

_STOPWORDS = {"a", "an", "the", "of", "in", "on", "for", "with", "to", "and", "or", "is", "are"}


def normalize_tokens(text: str) -> set[str]:
    """Lowercase, strip punctuation, drop stopwords -> a bag of significant words.

    Used to compare a free-text concept name (typed by a student, or lifted
    from a different course's material) against this dataset's concept names,
    since slugs are course-local and never overlap here (see graph note above)
    — a token-overlap match is the only thing that CAN bridge two independently
    extracted name spaces without inventing a shared vocabulary.
    """
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return {t for t in text.split() if t and t not in _STOPWORDS}


def names_match(a: str, b: str) -> bool:
    """True if the two concept names are the same idea under normalization.

    Exact bag-of-words match after stopword removal — deliberately NOT a loose
    partial-overlap (e.g. "matrix" would then match "confusion matrix", which
    are unrelated). A real synonym match ("clustering" / "cluster analysis")
    needs model judgement; see the known-limitation note in the design doc.
    """
    ta, tb = normalize_tokens(a), normalize_tokens(b)
    return bool(ta) and ta == tb


class UnknownCourseError(KeyError):
    pass


@dataclass
class Concept:
    slug: str
    name: str
    kind: str
    course: str


@dataclass
class Course:
    code: str
    title: str
    ects: int
    internal_prerequisites: list[str]
    external_prerequisites: list[str]
    concepts: list[Concept] = field(default_factory=list)


class CurriculumGraph:
    def __init__(self, data_dir: Path, metadata_path: Path):
        graph = json.loads((data_dir / "concept_graph.json").read_text(encoding="utf-8"))
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))["courses"]

        titles = {c["course_code"]: c["title"] for c in graph["courses"]}
        self.courses: dict[str, Course] = {}
        for code, title in titles.items():
            m = meta.get(code, {})
            self.courses[code] = Course(
                code=code, title=title,
                ects=m.get("ects", 0),
                internal_prerequisites=list(m.get("internal_prerequisites", [])),
                external_prerequisites=list(m.get("external_prerequisites", [])),
            )
        # metadata entries for courses no longer present in the graph are just
        # unused; a metadata entry MISSING for a course the graph does have
        # would silently zero its ECTS, which is worth catching at load time.
        missing = set(titles) - set(meta)
        if missing:
            raise RuntimeError(f"course_metadata.json has no entry for: {sorted(missing)}")

        for c in graph["concepts"]:
            for course_code in c["courses"].split("|"):
                if course_code in self.courses:
                    self.courses[course_code].concepts.append(
                        Concept(slug=c["slug"], name=c["name"], kind=c["kind"], course=course_code))

    def course(self, code: str) -> Course:
        try:
            return self.courses[code.upper()]
        except KeyError:
            raise UnknownCourseError(code) from None

    def known_course_codes(self) -> list[str]:
        return sorted(self.courses)

    def match_known_concepts(self, course_code: str, known: list[str]) -> tuple[list[str], list[str]]:
        """Split a course's concept names into (covered, residual) given a
        free-text list of concepts the caller says are already known."""
        course = self.course(course_code)
        covered, residual = [], []
        for concept in course.concepts:
            if any(names_match(concept.name, k) for k in known):
                covered.append(concept.name)
            else:
                residual.append(concept.name)
        return covered, residual

    def related_concepts(self, course_a: str, course_b: str) -> list[tuple[str, str]]:
        a, b = self.course(course_a), self.course(course_b)
        pairs = []
        for ca in a.concepts:
            for cb in b.concepts:
                if names_match(ca.name, cb.name):
                    pairs.append((ca.name, cb.name))
        return pairs

    def topological_order(self, target_courses: list[str]) -> tuple[list[str], list[str]]:
        """Kahn's algorithm over internal_prerequisites, restricted to the
        given course set. Returns (order, cycle_courses); cycle_courses is
        non-empty only if the restricted subgraph has a cycle."""
        target = list(dict.fromkeys(target_courses))  # de-dup, keep order
        target_set = set(target)
        indegree = {c: 0 for c in target}
        edges: dict[str, list[str]] = {c: [] for c in target}
        for c in target:
            for prereq in self.courses[c].internal_prerequisites:
                if prereq in target_set:
                    edges[prereq].append(c)
                    indegree[c] += 1
        ready = sorted(c for c in target if indegree[c] == 0)
        order = []
        while ready:
            n = ready.pop(0)
            order.append(n)
            for m in edges[n]:
                indegree[m] -= 1
                if indegree[m] == 0:
                    ready.append(m)
                    ready.sort()
        remaining = [c for c in target if c not in order]
        return order, remaining


_INSTANCE: CurriculumGraph | None = None


def get_graph() -> CurriculumGraph:
    global _INSTANCE
    if _INSTANCE is None:
        data_dir = Path(os.environ.get("CURRICULUM_DATA_DIR", DEFAULT_DATA_DIR))
        _INSTANCE = CurriculumGraph(data_dir, METADATA_PATH)
    return _INSTANCE
