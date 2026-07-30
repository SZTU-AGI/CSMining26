from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from freca.models import EscalationTier


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathsConfig(StrictConfig):
    cases_root: Path
    policy_pdf: Path
    checkpoints_xlsx: Path
    submission_template: Path
    build_dir: Path
    signature_truth_xlsx: Path | None = None


class ResponseFormatMode(StrEnum):
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    NONE = "none"


class RecallMode(StrEnum):
    BM25 = "bm25"
    VECTOR = "vector"
    HYBRID = "hybrid"


class FusionMode(StrEnum):
    NONE = "none"
    WEIGHTED = "weighted"
    RRF = "rrf"


class RerankerMode(StrEnum):
    NONE = "none"
    LEXICAL = "lexical"
    CROSS_ENCODER_API = "cross_encoder_api"
    LLM_LISTWISE = "llm_listwise"


class SelectorMode(StrEnum):
    TOP_K = "top_k"
    MMR = "mmr"
    SOURCE_AWARE_MMR = "source_aware_mmr"


class RetrievalAgentMode(StrEnum):
    DISABLED = "disabled"
    HEURISTIC = "heuristic"
    LLM = "llm"
    PLANNER = "planner"
    CRITIC = "critic"
    PLANNER_CRITIC = "planner_critic"


class RetrievalConfig(StrictConfig):
    recall_mode: RecallMode = RecallMode.HYBRID
    fusion_mode: FusionMode = FusionMode.RRF
    reranker_mode: RerankerMode = RerankerMode.LEXICAL
    selector_mode: SelectorMode = SelectorMode.SOURCE_AWARE_MMR
    bm25_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    vector_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    fusion_weight: float = Field(default=0.45, ge=0.0, le=1.0)
    reranker_weight: float = Field(default=0.55, ge=0.0, le=1.0)
    rrf_k: int = Field(default=60, ge=1)
    candidate_limit: int = Field(default=40, ge=1)
    mmr_lambda: float = Field(default=0.65, ge=0.0, le=1.0)
    same_source_penalty: float = Field(default=0.5, ge=0.0)
    same_track_penalty: float = Field(default=0.15, ge=0.0)
    same_location_penalty: float = Field(default=0.1, ge=0.0)
    min_unique_sources: int = Field(default=2, ge=1)
    agent_mode: RetrievalAgentMode = RetrievalAgentMode.HEURISTIC
    max_repairs: int = Field(default=2, ge=0, le=2)
    planner_max_rounds: int = Field(default=1, ge=1, le=2)
    critic_min_confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class ModelEndpointConfig(StrictConfig):
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: float = 120.0
    max_retries: int = 3
    response_format: ResponseFormatMode = ResponseFormatMode.JSON_SCHEMA


class MinerUMode(StrEnum):
    DISABLED = "disabled"
    CLOUD_SDK = "cloud_sdk"
    REMOTE_API = "remote_api"


class MinerUConfig(StrictConfig):
    mode: MinerUMode = MinerUMode.DISABLED
    base_url: str = "https://mineru.net/api/v4"
    token_env: str | None = "MINERU_TOKEN"
    model: str = "vlm"
    language: str = "en"
    ocr: bool = True
    formula: bool = True
    table: bool = True
    timeout_seconds: float = 600.0
    max_retries: int = 3


class ModelsConfig(StrictConfig):
    audit: ModelEndpointConfig
    verifier: ModelEndpointConfig | None = None
    arbitrator: ModelEndpointConfig | None = None
    query_rewriter: ModelEndpointConfig | None = None
    embedding: ModelEndpointConfig | None = None
    vision: ModelEndpointConfig | None = None
    reranker: ModelEndpointConfig | None = None
    retrieval_agent: ModelEndpointConfig | None = None
    planner: ModelEndpointConfig | None = None
    critic: ModelEndpointConfig | None = None
    tiebreaker: ModelEndpointConfig | None = None


class ArbitrationConfig(StrictConfig):
    tier: EscalationTier = EscalationTier.BLIND
    confidence_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    tiebreaker_required_on_disagreement: bool = True


class PipelineConfig(StrictConfig):
    paths: PathsConfig
    models: ModelsConfig
    mineru: MinerUConfig = MinerUConfig()
    retrieval: RetrievalConfig = RetrievalConfig()
    arbitration: ArbitrationConfig = ArbitrationConfig()

    @classmethod
    def from_yaml(cls, path: Path) -> PipelineConfig:
        path = path.resolve()
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        config = cls.model_validate(raw)
        base = path.parent
        resolved = {
            name: value if value.is_absolute() else (base / value).resolve()
            for name, value in config.paths.model_dump().items()
            if isinstance(value, Path)
        }
        # 非 Path 字段(如 signature_truth_xlsx: null)保留原值
        not_resolved = {
            name: value
            for name, value in config.paths.model_dump().items()
            if not isinstance(value, Path)
        }
        merged = {**not_resolved, **resolved}
        config = config.model_copy(update={"paths": PathsConfig(**merged)})
        return cls._apply_audit_env_overrides(config)

    @classmethod
    def _apply_audit_env_overrides(cls, config: PipelineConfig) -> PipelineConfig:
        """Override models.audit.base_url/model from env vars when present.

        Lets operators switch the audit endpoint (e.g. to a private MiniMax
        deployment) without editing the tracked config.yaml. Existing values
        are kept unless the env var is set, so CI / tests without the env
        still load the YAML defaults.
        """
        import os

        overrides: dict[str, str] = {}
        base_url = os.environ.get("FRECA_AUDIT_BASE_URL")
        model = os.environ.get("FRECA_AUDIT_MODEL")
        if base_url:
            overrides["base_url"] = base_url
        if model:
            overrides["model"] = model
        if not overrides:
            return config
        audit = config.models.audit.model_copy(update=overrides)
        models = config.models.model_copy(update={"audit": audit})
        return config.model_copy(update={"models": models})
