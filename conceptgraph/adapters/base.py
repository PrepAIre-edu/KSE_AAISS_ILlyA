"""Source adapters: the ONLY source-specific code in the pipeline.

An adapter's single job is to turn a file path inside a dump into structural
metadata. Everything downstream (bundling, extraction, consolidation, export)
consumes SourceFile and never looks at the original naming convention.

To support a new provider, subclass SourceAdapter, implement parse(), and
register it. Nothing else changes.
"""
from __future__ import annotations
import hashlib
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path

ASSET_KINDS = {
    "video_transcript", "podcast_transcript", "notes", "lesson", "casebook",
    "infographic_transcript", "handbook", "reading", "assignment", "quiz",
    "spreadsheet", "other",
}


@dataclass
class SourceFile:
    rel_path: str
    filename: str
    course_code: str
    course_title: str
    module_ordinal: int
    unit_ordinal: int
    asset_kind: str
    asset_ordinal: int | None = None
    part_ordinal: int | None = None
    sha256: str = ""
    bytes: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def module_code(self) -> str:
        return "OM" if self.module_ordinal == 0 else f"M{self.module_ordinal}"

    @property
    def unit_code(self) -> str:
        return f"{self.module_code}U{self.unit_ordinal}"

    @property
    def unit_id(self) -> str:
        return f"{self.course_code}-{self.unit_code}"

    @property
    def text_key(self) -> str:
        """Flat filename for the extracted-text cache."""
        return self.rel_path.replace("/", "~").rsplit(".", 1)[0] + ".txt"

    def to_dict(self):
        d = asdict(self)
        d.update(unit_id=self.unit_id, unit_code=self.unit_code, module_code=self.module_code)
        return d


class SourceAdapter(ABC):
    name: str = "base"
    #: extensions the adapter is willing to ingest
    extensions: tuple[str, ...] = (".pdf", ".txt", ".md", ".docx", ".xlsx")
    #: directory names never treated as source content (our own outputs, VCS, venvs)
    ignore_dirs: tuple[str, ...] = ("_concept_graph", "__pycache__", "node_modules", "venv", ".venv")

    @abstractmethod
    def parse(self, rel_path: str) -> dict:
        """Return dict with at least: course_code, course_title, module_ordinal,
        unit_ordinal, asset_kind. May include asset_ordinal / part_ordinal."""

    def course_title(self, code: str) -> str:
        return code

    def scan(self, root: Path) -> list[SourceFile]:
        out: list[SourceFile] = []
        for dirpath, dirs, names in os.walk(root):
            # prune in place so os.walk does not descend into them at all
            dirs[:] = [d for d in sorted(dirs)
                       if not d.startswith((".", "_")) and d not in self.ignore_dirs]
            for n in sorted(names):
                if n.startswith(".") or not n.lower().endswith(self.extensions):
                    continue
                full = Path(dirpath) / n
                rel = str(full.relative_to(root)).replace("\\", "/")
                try:
                    meta = self.parse(rel)
                except Exception as e:                       # noqa: BLE001
                    meta = {"course_code": rel.split("/")[0], "module_ordinal": 0,
                            "unit_ordinal": 0, "asset_kind": "other",
                            "metadata": {"parse_error": str(e)}}
                meta.setdefault("course_title", self.course_title(meta["course_code"]))
                if meta["asset_kind"] not in ASSET_KINDS:
                    meta["asset_kind"] = "other"
                data = full.read_bytes()
                out.append(SourceFile(
                    rel_path=rel, filename=n,
                    sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                    **{k: v for k, v in meta.items() if k in SourceFile.__dataclass_fields__
                       and k not in {"rel_path", "filename", "sha256", "bytes"}},
                ))
        out.sort(key=lambda f: (f.course_code, f.module_ordinal, f.unit_ordinal,
                                f.asset_ordinal or 0, f.part_ordinal or 0, f.filename))
        return out


_REGISTRY: dict[str, type[SourceAdapter]] = {}


def register(cls: type[SourceAdapter]) -> type[SourceAdapter]:
    _REGISTRY[cls.name] = cls
    return cls


def get_adapter(name: str) -> SourceAdapter:
    if name not in _REGISTRY:
        raise KeyError(f"unknown adapter {name!r}; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name]()


def registered() -> list[str]:
    return sorted(_REGISTRY)
