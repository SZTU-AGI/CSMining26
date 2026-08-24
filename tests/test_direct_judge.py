import pytest

from freca.direct_judge import build_direct_envelope, decision_from_direct_payload
from freca.models import CheckpointDefinition

from test_pipeline_quality import _chunk, _payload


def _checkpoint() -> CheckpointDefinition:
    return CheckpointDefinition(
        cp_id="CP1",
        element_id=1,
        element_title="Element 1",
        section_title="Section 1",
        text="Registration must cover the operation.",
        source_file="cp.xlsx",
        cell="A1",
    )


def test_checkpoint_full_uses_only_current_case_and_policy_chunks() -> None:
    envelope = build_direct_envelope(
        method="checkpoint_full_judge",
        case_id=23,
        checkpoint=_checkpoint(),
        policy_chunks=[_chunk("p1", "policy", case_id=None)],
        case_chunks=[
            _chunk("case-023", "current case", case_id=23),
            _chunk("case-024", "foreign case", case_id=24),
        ],
    )

    assert "case-023" in envelope.text
    assert "case-024" not in envelope.text


def test_direct_judge_rejects_annotated_or_unknown_citations() -> None:
    payload = _payload(confidence=0.9)
    payload["case_id"] = 23
    payload["supporting_evidence"] = ["unknown-id: annotation"]

    with pytest.raises(ValueError, match="citation"):
        decision_from_direct_payload(
            payload,
            allowed_policy_ids={"p1"},
            allowed_evidence_ids={"case-023"},
        )
