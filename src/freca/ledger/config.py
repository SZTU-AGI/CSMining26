"""Configuration for the ledger architecture.

The ledger stack reuses the existing :class:`freca.config.PipelineConfig`
verbatim (paths, MinerU, retrieval, model endpoints) and layers its own
``ledger:`` block on top. ``PipelineConfig`` forbids unknown keys, so the block
is split off *before* validation. That keeps the legacy ``config.yaml`` valid
for the legacy CLI, while ``config.ledger.yaml`` drives the new one.

Switching architectures is therefore a config choice, not a code change:

* legacy   ``freca --config config.yaml ...``
* ledger   ``python -m freca.ledger --config config.ledger.yaml ...``
"""

from __future__ import annotations

import copy
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from freca.config import (
    ModelEndpointConfig,
    PathsConfig,
    PipelineConfig,
    StrictConfig,
)
from freca.env_loader import apply_env_file, find_env_file

LEDGER_SECTION = "ledger"


class ExtractorMode(StrEnum):
    """How facts are produced from parsed chunks."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"
    LLM_WITH_FALLBACK = "llm_with_fallback"


class ReviewMode(StrEnum):
    DISABLED = "disabled"
    ON_TRIGGER = "on_trigger"
    ALWAYS = "always"


class ExtractionConfig(StrictConfig):
    mode: ExtractorMode = ExtractorMode.LLM_WITH_FALLBACK
    batch_char_budget: int = Field(default=12000, ge=1000)
    max_chunks_per_batch: int = Field(default=25, ge=1)
    max_facts_per_batch: int = Field(default=40, ge=1)
    require_verbatim_match: bool = True
    verbatim_min_length: int = Field(default=8, ge=1)
    drop_answer_like_facts: bool = True
    max_workers: int = Field(default=4, ge=1)
    # Deterministic segmentation budget. Facts are ranked by informativeness
    # (dates, measurements, identity markers, topic keywords) before truncation,
    # and every truncation is recorded in the ledger's quality_flags.
    max_facts_per_chunk: int = Field(default=8, ge=1)
    max_facts_per_case: int = Field(default=900, ge=1)
    min_segment_chars: int = Field(default=12, ge=1)
    segment_char_limit: int = Field(default=900, ge=100)


class RubricConfig(StrictConfig):
    policy_limit: int = Field(default=12, ge=1)
    max_criteria: int = Field(default=10, ge=1)
    snippet_char_limit: int = Field(default=1800, ge=200)
    cache_enabled: bool = True
    max_workers: int = Field(default=4, ge=1)


class SelectionConfig(StrictConfig):
    max_facts: int = Field(default=28, ge=1)
    min_facts_per_criterion: int = Field(default=2, ge=0)
    include_all_contradictions: bool = True
    topic_bonus: float = Field(default=1.5, ge=0.0)
    category_bonus: float = Field(default=1.0, ge=0.0)
    include_answer_like: bool = False
    include_contaminated: bool = True
    verbatim_char_limit: int = Field(default=600, ge=80)


class AdjudicationConfig(StrictConfig):
    confidence_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    require_dual_citation: bool = True
    scope_aware_evidence: bool = False
    max_workers: int = Field(default=4, ge=1)


class ReviewConfig(StrictConfig):
    mode: ReviewMode = ReviewMode.ON_TRIGGER
    max_facts: int = Field(default=14, ge=1)
    snippet_char_limit: int = Field(default=1200, ge=200)
    prefer_review_on_conflict: bool = True


class CriticConfig(StrictConfig):
    enabled: bool = False
    max_facts: int = Field(default=14, ge=1)
    snippet_char_limit: int = Field(default=1200, ge=200)


class BaselineConfig(StrictConfig):
    require_distinct_views: bool = True
    min_distinct_views: int = Field(default=2, ge=1)
    min_agreeing_methods: int = Field(default=2, ge=1)
    # 漏洞1 门禁:review_priority 达到该阈值、且未经独立复核的 verdict 不得进入
    # production_candidate 的可提交子集(计入 held_back)。已复核(review 已确认或
    # 推翻 primary)的项放行。0.5 = 证据弱过半即视为高风险未决;此为先验默认,接
    # 模型后应用金标校准(见 OPTIMAL_PIPELINE_DELIVERY §5 漏洞 4)。
    production_priority_threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class LedgerModelsConfig(StrictConfig):
    """Optional per-stage endpoints; each falls back to a legacy endpoint."""

    extractor: ModelEndpointConfig | None = None
    rubric: ModelEndpointConfig | None = None
    adjudicator: ModelEndpointConfig | None = None
    reviewer: ModelEndpointConfig | None = None
    critic: ModelEndpointConfig | None = None


class LedgerSettings(StrictConfig):
    output_dirname: str = "ledger"
    extraction: ExtractionConfig = ExtractionConfig()
    rubric: RubricConfig = RubricConfig()
    selection: SelectionConfig = SelectionConfig()
    adjudication: AdjudicationConfig = AdjudicationConfig()
    review: ReviewConfig = ReviewConfig()
    critic: CriticConfig = CriticConfig()
    baseline: BaselineConfig = BaselineConfig()
    models: LedgerModelsConfig = LedgerModelsConfig()


# Stage -> legacy endpoint fallback order.
_FALLBACKS: dict[str, tuple[str, ...]] = {
    "extractor": ("audit",),
    "rubric": ("audit",),
    "adjudicator": ("audit",),
    "reviewer": ("verifier", "arbitrator", "audit"),
    "critic": ("arbitrator", "verifier", "audit"),
}


def _resolve_paths(config: PipelineConfig, base: Path) -> PipelineConfig:
    """Replicate ``PipelineConfig.from_yaml`` relative-path resolution."""

    dumped = config.paths.model_dump()
    resolved = {
        name: value if value.is_absolute() else (base / value).resolve()
        for name, value in dumped.items()
        if isinstance(value, Path)
    }
    passthrough = {
        name: value for name, value in dumped.items() if not isinstance(value, Path)
    }
    merged = {**passthrough, **resolved}
    return config.model_copy(update={"paths": PathsConfig(**merged)})


class LedgerConfig(StrictConfig):
    """Composite configuration: legacy pipeline + ledger settings."""

    pipeline: PipelineConfig
    ledger: LedgerSettings = LedgerSettings()
    source_path: Path | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> LedgerConfig:
        path = Path(path).resolve()
        env_path = find_env_file(path.parent)
        if env_path is not None:
            apply_env_file(env_path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"configuration root must be a mapping: {path}")
        raw = copy.deepcopy(raw)
        ledger_raw = raw.pop(LEDGER_SECTION, None) or {}
        pipeline = PipelineConfig.model_validate(raw)
        pipeline = _resolve_paths(pipeline, path.parent)
        return cls(
            pipeline=pipeline,
            ledger=LedgerSettings.model_validate(ledger_raw),
            source_path=path,
        )

    @classmethod
    def from_pipeline(
        cls,
        pipeline: PipelineConfig,
        settings: LedgerSettings | None = None,
    ) -> LedgerConfig:
        return cls(pipeline=pipeline, ledger=settings or LedgerSettings())

    # -- derived paths ----------------------------------------------------

    @property
    def build_dir(self) -> Path:
        return self.pipeline.paths.build_dir

    @property
    def ledger_dir(self) -> Path:
        return self.build_dir / self.ledger.output_dirname

    # -- endpoints --------------------------------------------------------

    def endpoint(self, stage: str) -> ModelEndpointConfig | None:
        """Resolve the endpoint for a ledger stage, falling back to legacy."""

        if stage not in _FALLBACKS:
            raise KeyError(f"unknown ledger stage: {stage}")
        override = getattr(self.ledger.models, stage, None)
        if override is not None:
            return override
        for name in _FALLBACKS[stage]:
            endpoint = getattr(self.pipeline.models, name, None)
            if endpoint is not None:
                return endpoint
        return None

    def endpoint_origin(self, stage: str) -> str:
        if getattr(self.ledger.models, stage, None) is not None:
            return f"ledger.models.{stage}"
        for name in _FALLBACKS[stage]:
            if getattr(self.pipeline.models, name, None) is not None:
                return f"models.{name}"
        return "unconfigured"

    def describe(self) -> dict[str, Any]:
        return {
            "config_path": str(self.source_path) if self.source_path else None,
            "build_dir": str(self.build_dir),
            "ledger_dir": str(self.ledger_dir),
            "extraction_mode": self.ledger.extraction.mode.value,
            "review_mode": self.ledger.review.mode.value,
            "endpoints": {
                stage: {
                    "origin": self.endpoint_origin(stage),
                    "model": getattr(self.endpoint(stage), "model", None),
                }
                for stage in _FALLBACKS
            },
        }


__all__ = [
    "AdjudicationConfig",
    "BaselineConfig",
    "CriticConfig",
    "ExtractionConfig",
    "ExtractorMode",
    "LEDGER_SECTION",
    "LedgerConfig",
    "LedgerModelsConfig",
    "LedgerSettings",
    "ReviewConfig",
    "ReviewMode",
    "RubricConfig",
    "SelectionConfig",
]
