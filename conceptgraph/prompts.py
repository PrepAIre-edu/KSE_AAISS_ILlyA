"""All model-facing text lives here, so behaviour can be tuned without touching
orchestration code."""
from __future__ import annotations

EXTRACT_SYSTEM = """\
You extract a concept graph from course material. You are precise, conservative, \
and you never invent quotes.

A CONCEPT is a discrete piece of transferable knowledge a learner can be said to \
"know" — in linear algebra that would be "matrix" or "Gaussian elimination"; in a \
business course "network effects", "Matrix of Change", "transaction cost"; in a \
neuroscience course "neuroplasticity", "working memory".

These are NOT concepts: course logistics, instructor or author names, companies used \
only as illustration, section headings, generic words ("strategy", "data", "brain"), \
and restatements of learning outcomes.

Rules you must not break:
1. {min_c}-{max_c} concepts per unit. Never fewer than 4, never more than {hard_max}.
2. `slug` is a canonical, SOURCE-INDEPENDENT kebab-case English name. Two units \
teaching the same idea must produce the identical slug. Use the standard textbook \
name, not the phrasing of this particular file.
3. Every concept needs at least one occurrence whose `quote` is copied VERBATIM from \
the text ({min_q}-{max_q} characters) and whose `rel_path` is the exact path shown in \
the "### FILE:" marker above that text. Never paraphrase a quote. Never merge text \
from two files into one quote.
4. Propose links between concepts, roughly {links_per} per concept. Prefer \
`prerequisite` and `part_of` over `related`. `prerequisite` means src must be \
understood BEFORE dst, and must stay acyclic. You may link to a concept in another \
unit if you are confident of its canonical slug.
"""

EXTRACT_USER = """\
Extract concepts for {n} unit(s). Return one entry in `units` per unit, using the \
exact unit_id given in each header.

{bundles}
"""

MERGE_SYSTEM = """\
You decide whether pairs of extracted concepts denote the SAME thing and should \
collapse into one node.

Merge only true synonyms or trivial rewordings — "cluster-analysis"/"clustering", \
"brain-plasticity"/"neuroplasticity", "word-of-mouth-marketing"/"referral-marketing".

Do NOT merge:
 * a general concept with a specific instance of it (that is a `part_of` link)
 * two concepts that are merely related, adjacent, or frequently discussed together
 * opposites or complements ("demand curve"/"supply curve", "precision"/"recall")

When same=True, set `canonical` to whichever slug is the more standard textbook name. \
Return a verdict for every pair you are given.
"""

MERGE_USER = """\
Adjudicate these {n} candidate pairs. Each block shows both concepts with kind, \
courses, aliases and definition.

{pairs}
"""

PROPOSE_MERGE_SYSTEM = MERGE_SYSTEM + """
Here you are not given candidate pairs — you are given a slice of the catalogue and \
must FIND the duplicates yourself. Lexical filters already caught the obvious ones, \
so look for pairs worded completely differently that mean the same thing (for example \
"computer as tool" and "AI as tool", or "four roles of computers" and "tool-assistant\
-peer-manager framework"). Return only pairs you are confident about; returning \
nothing is a valid answer.
"""

PROPOSE_MERGE_USER = """\
Find duplicate concepts within this slice of the catalogue ({n} concepts). Return \
verdicts only for pairs where same=True.

{catalogue}
"""

BRIDGE_SYSTEM = """\
You add missing edges to a concept graph.

The graph was extracted unit by unit in parallel, so no extractor could see the whole \
catalogue. Concepts that belong together across different modules or different courses \
were therefore never linked. Your job is to add those edges.

Prioritise, in order:
 1. edges connecting concepts from DIFFERENT courses
 2. edges into or out of concepts that currently have few or no links
 3. edges that make a teaching order explicit (`prerequisite`)

Link types:
 * prerequisite   — src must be understood BEFORE dst. Must not create a cycle.
 * part_of        — src is a component of dst
 * applies_to     — src is applied to / operationalised on dst
 * contrasts_with — competing or opposing ideas
 * related        — weak association; use sparingly

Use ONLY slugs that appear in the material you are given. Every link needs a one-clause \
rationale. Do not restate links that already exist.
"""

BRIDGE_USER = """\
Propose up to {k} new links. `src` and `dst` must both be slugs from the index below.

## Concepts in this slice (full detail)
{slice_detail}

## Existing links touching this slice — do not repeat these
{existing}

## Global slug index (every concept in the graph; you may link to any of these)
{index}
"""
