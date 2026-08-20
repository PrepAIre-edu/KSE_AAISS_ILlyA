"""Runtime settings. Everything tunable lives here, nothing is hardcoded downstream."""
from __future__ import annotations
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Anthropic pricing, USD per 1M tokens. Update when pricing changes.
PRICES = {
    "claude-opus-5":          {"in": 15.00, "out": 75.00},
    "claude-sonnet-5":        {"in":  3.00, "out": 15.00},
    "claude-haiku-4-5-20251001": {"in": 1.00, "out": 5.00},
}


@dataclass
class Settings:
    # --- io ---------------------------------------------------------------
    source_root: Path                       # folder holding the course dump
    work_dir: Path                          # all intermediate + final artifacts
    adapter: str = "mit"                    # registered SourceAdapter name
    courses: tuple[str, ...] = ()           # restrict to these course codes; () = all

    # --- backend ----------------------------------------------------------
    backend: str = "anthropic"       # anthropic | ollama | openai
    base_url: str | None = None      # for backend="openai"
    api_key_env: str = "LLM_API_KEY"
    local_num_ctx: int = 32768       # context window of the local model

    # --- models -----------------------------------------------------------
    # map stage runs many times on large inputs -> the cheaper capable model.
    extract_model: str = "claude-sonnet-5"
    # reduce stage runs a handful of times and needs global judgement.
    consolidate_model: str = "claude-opus-5"
    max_tokens_out: int = 16000
    temperature: float = 0.0

    # --- extraction contract ---------------------------------------------
    min_concepts_per_unit: int = 5
    max_concepts_per_unit: int = 10
    hard_max_concepts_per_unit: int = 12    # tolerated for long academic readings
    min_quote_chars: int = 20
    max_quote_chars: int = 300
    target_links_per_concept: float = 2.0

    # --- batching ---------------------------------------------------------
    # chars per LLM call for the map stage; ~4 chars/token.
    batch_target_chars: int = 160_000
    max_workers: int = 8

    # --- consolidation ----------------------------------------------------
    merge_candidate_cutoff: float = 0.86    # normalised-name similarity
    max_merge_pairs: int = 600
    merge_pairs_per_call: int = 60
    bridge_slice_size: int = 45             # concepts detailed per bridge call
    bridge_links_per_slice: int = 14

    # --- quality gate -----------------------------------------------------
    min_quote_verbatim_rate: float = 0.90
    min_unit_coverage: float = 0.95         # units that produced any concept
    fail_on_gate: bool = True

    # --- ops --------------------------------------------------------------
    cache_dir: Path | None = None           # defaults to work_dir/.cache
    max_retries: int = 4
    skip_module_ordinals: tuple[int, ...] = (0,)   # orientation = admin content
    min_file_chars: int = 200               # ignore near-empty extractions
    # True for transcripts/prose; False for table-heavy documents (syllabi).
    # See textract._pdftotext for why this matters to quote verbatimness.
    pdf_layout: bool = True

    def __post_init__(self):
        # On a non-Anthropic backend a Claude model name is always a mistake:
        # reuse whatever model was actually named for extraction.
        if self.backend != "anthropic":
            if self.consolidate_model.startswith("claude"):
                self.consolidate_model = self.extract_model
            if self.extract_model.startswith("claude"):
                raise ValueError(
                    f"backend={self.backend!r} but extract_model={self.extract_model!r}; "
                    "pass --extract-model with a model your backend serves "
                    "(e.g. qwen2.5:14b-instruct)")
        # A local 14B model with a 32k window cannot take a batch sized for
        # Claude: 160k chars is ~40k tokens. Leave room for the system prompt
        # and the JSON answer, so cap the batch at ~45% of the window.
        if self.backend in {"ollama", "openai"} and \
                self.batch_target_chars > self.local_num_ctx * 4 * 0.45:
            self.batch_target_chars = int(self.local_num_ctx * 4 * 0.45)
        self.source_root = Path(self.source_root)
        self.work_dir = Path(self.work_dir)
        self.cache_dir = Path(self.cache_dir) if self.cache_dir else self.work_dir / ".cache"
        for p in (self.work_dir, self.cache_dir):
            p.mkdir(parents=True, exist_ok=True)

    @property
    def api_key(self) -> str:
        k = os.environ.get("ANTHROPIC_API_KEY")
        if not k:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        return k

    def path(self, *parts) -> Path:
        p = self.work_dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def to_dict(self):
        d = asdict(self)
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in d.items()}
