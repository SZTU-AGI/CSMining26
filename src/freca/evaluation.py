from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from freca.models import Verdict
from freca.state import read_json


class GoldLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: int = Field(ge=1, le=100)
    cp_id: str = Field(pattern=r"^CP(?:[1-9]|[1-3][0-9]|4[01])$")
    verdict: Verdict
    confirmed: bool
    note: str


def load_gold_labels(path: Path) -> dict[tuple[int, str], GoldLabel]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("labels"), list):
        raise ValueError("gold label file must contain a labels list")

    labels: dict[tuple[int, str], GoldLabel] = {}
    for raw in payload["labels"]:
        label = GoldLabel.model_validate(raw)
        if not label.confirmed:
            continue
        key = (label.case_id, label.cp_id)
        if key in labels:
            raise ValueError(
                f"duplicate confirmed gold label: {label.case_id}/{label.cp_id}"
            )
        labels[key] = label
    return labels
