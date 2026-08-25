from freca.ledger.adjudicate import build_adjudication_messages

from ledger_helpers import make_fact, make_pack, make_rubric


def test_scope_aware_prompt_separates_design_and_execution_evidence() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])

    system, _ = build_adjudication_messages(
        rubric=rubric,
        pack=pack,
        scope_aware=True,
    )

    assert "execution incident" in system
    assert "design or facility requirement" in system


def test_default_prompt_keeps_existing_contract() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])

    system, _ = build_adjudication_messages(rubric=rubric, pack=pack)

    assert "execution incident" not in system
