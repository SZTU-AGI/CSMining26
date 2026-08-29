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


# -- v5 evidence-discipline rules ------------------------------------------------


def test_misfiled_rule_adds_exclusion_contract() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])

    system, _ = build_adjudication_messages(
        rubric=rubric, pack=pack, misfiled_evidence_rule=True
    )

    flat = " ".join(system.split())
    assert "misfiled material" in flat
    assert "exclude its facts from the evidence base entirely" in flat
    assert "never by itself a ground for a non-compliant verdict" in flat


def test_design_atomicity_rule_separates_transient_condition() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])

    system, _ = build_adjudication_messages(
        rubric=rubric, pack=pack, design_atomicity_rule=True
    )

    flat = " ".join(system.split())
    assert "transient operating state" in flat
    assert "not a design breach" in flat
    assert "not disqualified as design evidence" in flat


def test_v5_flags_off_keep_prompt_byte_identical() -> None:
    rubric = make_rubric()
    pack = make_pack(rubric=rubric, facts=[make_fact("f1")])

    base_system, base_user = build_adjudication_messages(rubric=rubric, pack=pack)
    off_system, off_user = build_adjudication_messages(
        rubric=rubric,
        pack=pack,
        misfiled_evidence_rule=False,
        design_atomicity_rule=False,
    )

    assert (off_system, off_user) == (base_system, base_user)


def test_adjudication_config_v5_rule_defaults_off() -> None:
    from freca.ledger.config import AdjudicationConfig

    config = AdjudicationConfig()

    assert config.misfiled_evidence_rule is False
    assert config.design_atomicity_rule is False
