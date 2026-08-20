"""The contract between the LLM and the pipeline.

Every LLM call is forced through a tool_use input_schema derived from these
models, so we never parse prose or strip markdown fences.
"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, field_validator

ConceptKind = Literal["definition", "method", "framework", "principle", "metric", "tool", "case"]
OccurrenceRole = Literal["introduced", "defined", "applied", "mentioned", "assessed"]
LinkType = Literal["prerequisite", "part_of", "applies_to", "contrasts_with", "related"]

SYMMETRIC: set[str] = {"contrasts_with", "related"}


class Occurrence(BaseModel):
    rel_path: str = Field(description="Exact source file path this quote came from")
    role: OccurrenceRole = "mentioned"
    quote: str = Field(description="Verbatim span copied from the source text")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class Concept(BaseModel):
    slug: str = Field(description="Canonical source-independent kebab-case English name")
    name: str
    kind: ConceptKind
    definition: str = Field(description="1-3 self-contained sentences")
    difficulty: int = Field(ge=1, le=5)
    domain: str | None = None
    aliases: list[str] = Field(default_factory=list)
    occurrences: list[Occurrence] = Field(min_length=1)

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        import re
        s = re.sub(r"[^a-z0-9]+", "-", v.lower()).strip("-")
        if not s:
            raise ValueError("empty slug")
        return re.sub(r"-{2,}", "-", s)


class Link(BaseModel):
    src: str
    dst: str
    type: LinkType
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: str = ""


class UnitExtraction(BaseModel):
    unit_id: str
    concepts: list[Concept]
    links: list[Link] = Field(default_factory=list)


class BatchExtraction(BaseModel):
    """Return value of one map-stage call."""
    units: list[UnitExtraction]


class MergeVerdict(BaseModel):
    slug_a: str
    slug_b: str
    same: bool = Field(description="True only if these denote the identical concept")
    canonical: str | None = Field(default=None, description="Which slug to keep when same=True")
    reason: str = ""


class MergeAdjudication(BaseModel):
    """Return value of one merge-adjudication call."""
    verdicts: list[MergeVerdict]


class BridgeLinks(BaseModel):
    """Return value of one bridge-link call."""
    links: list[Link]


def tool_schema(model: type[BaseModel]) -> dict:
    """Pydantic model -> Anthropic tool input_schema (inlined, no $ref)."""
    s = model.model_json_schema()
    defs = s.pop("$defs", {})

    def inline(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node.pop("$ref").split("/")[-1]
                node.update(inline(dict(defs[name])))
            # Anthropic's validator dislikes anyOf-with-null; flatten to the real type
            if "anyOf" in node:
                opts = [o for o in node["anyOf"] if o.get("type") != "null"]
                if len(opts) == 1:
                    node.pop("anyOf")
                    node.update(inline(dict(opts[0])))
                else:
                    node["anyOf"] = [inline(o) for o in node["anyOf"]]
            return {k: inline(v) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    return inline(s)
