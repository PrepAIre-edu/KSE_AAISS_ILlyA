"""Output contracts for every curriculum-mcp tool.

Every tool's *output* is a pydantic model defined here; its *input* is
declared inline in server.py as `Annotated[type, pydantic.Field(...)]`
parameters (the MCP server derives each tool's JSON input schema straight
from the function signature — see mcp.server.mcpserver.utilities.
func_metadata — so a wrapping input model here would just be unused
duplication). Either way, every field type/default/constraint below or in
server.py IS the real MCP schema, not a free-form string/dict. See
docs/TOOL_CONTRACTS.md for the human-readable version of the same contract.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

CourseCode = str  # one of CALC, LINALG, ML, PROB in this dataset; validated at runtime


# ---- validate_study_plan ---------------------------------------------------

class PrerequisiteViolation(BaseModel):
    course_code: str
    missing_prerequisite_course: str


class ExternalPrerequisiteNote(BaseModel):
    course_code: str
    note: str = Field(description="The course's own stated prerequisite, which this system cannot verify "
                                   "because the referenced course/skill is outside this dataset.")


class ValidateStudyPlanOutput(BaseModel):
    valid: bool = Field(description="False if any internal-prerequisite violation or ECTS-budget overrun exists.")
    total_ects: int = Field(description="Sum of ECTS for target_courses not already in completed_courses.")
    over_budget: bool
    internal_prerequisite_violations: list[PrerequisiteViolation] = Field(default_factory=list)
    external_prerequisite_notes: list[ExternalPrerequisiteNote] = Field(default_factory=list)
    recommended_order: list[str] = Field(description="target_courses topologically ordered by "
                                                       "internal prerequisites (stable order when none apply).")


# ---- detect_plan_conflicts --------------------------------------------------

class PlannedCourse(BaseModel):
    course_code: CourseCode
    term: int = Field(ge=1, description="1-based term/semester index the student assigns this course to.")


class Conflict(BaseModel):
    type: str = Field(description="'duplicate_course' | 'credit_overload' | 'prerequisite_order'")
    term: int | None = None
    courses: list[str]
    detail: str


class DetectPlanConflictsOutput(BaseModel):
    ok: bool = Field(description="True iff conflicts is empty.")
    conflicts: list[Conflict] = Field(default_factory=list)
    ects_by_term: dict[str, int] = Field(description="term number (as string) -> total ECTS scheduled in it.")


# ---- suggest_substitution (waiver-eligibility evaluator) --------------------

class SuggestSubstitutionOutput(BaseModel):
    course_code: str
    ects: int
    total_concepts: int
    covered_concepts: list[str]
    residual_concepts: list[str]
    residual_count: int
    waivable: bool = Field(description="True iff residual_count <= max_residual_concepts.")
    note: str


# ---- compare_courses (bonus) -------------------------------------------------

class RelatedConceptPair(BaseModel):
    concept_in_a: str
    concept_in_b: str


class CompareCoursesOutput(BaseModel):
    course_a: str
    course_b: str
    ects_a: int
    ects_b: int
    concept_count_a: int
    concept_count_b: int
    kind_distribution_a: dict[str, int]
    kind_distribution_b: dict[str, int]
    related_concepts: list[RelatedConceptPair] = Field(
        description="Concept name pairs that match after normalization; empty means the two "
                     "courses cover disjoint material under this (deliberately strict) comparison.")
    unique_to_a: list[str]
    unique_to_b: list[str]
    external_prerequisites_a: list[str]
    external_prerequisites_b: list[str]
