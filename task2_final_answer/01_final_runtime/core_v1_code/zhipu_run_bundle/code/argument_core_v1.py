#!/usr/bin/env python3
"""FRECA Core minimal Argument substrate v1.

Purpose
-------
This module is intentionally narrow.  It does NOT decide a final label and it
does NOT implement ProofStandard yet.

It reuses the frozen reference-core argument models and four-valued state, then
adds the missing direction-aware standing semantics required by FRECA D7.13.

Current minimal flow:

    DIRECT truth-bearing alignments
        -> ER observable raw_state

    CONDITIONAL alignments
        -> preserved separately, do NOT change ER raw_state

    ER ProofStandard accepted_state
        -> Argument premises

    ER1 + ER2 positive
        -> BENCHMARK_OPERATIONALIZATION(PRO)
        -> A1

    each ER negative
        -> RULE_VIOLATION(CON)
        -> A1

The raw-state argument run is diagnostic only.  Production argument evaluation
uses each ER's ProofStandard accepted_state.  With the current pilot proof gate,
that accepted state remains UNKNOWN.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


# ============================================================================
# Reference-core reuse
# ============================================================================


def _install_reference_core_path() -> Path:
    candidates: list[Path] = []

    env = os.environ.get(
        "FRECA_REFERENCE_CORE_SRC"
    )
    if env:
        candidates.append(
            Path(env)
        )

    here = Path(
        __file__
    ).resolve().parent

    candidates.extend(
        [
            Path(
                "/home/MeggieYu/freca/reference_core/"
                "freca_reference_core_20260828/src"
            ),
            here.parent
            / "reference_core"
            / "freca_reference_core_20260828"
            / "src",
            Path(
                "/mnt/data/freca_ref/"
                "freca_reference_core_20260828/src"
            ),
        ]
    )

    for candidate in candidates:
        if (
            candidate
            / "freca"
            / "argument"
            / "models.py"
        ).exists():
            sys.path.insert(
                0,
                str(candidate),
            )
            return candidate

    raise RuntimeError(
        "Could not locate freca_reference_core_20260828/src. "
        "Set FRECA_REFERENCE_CORE_SRC to the reference-core src directory."
    )


REFERENCE_CORE_SRC = (
    _install_reference_core_path()
)

from freca.argument.models import (  # noqa: E402
    ArgumentDirection,
    ArgumentPremiseTemplate,
    ArgumentTemplate,
    PremiseRole,
    RequiredPolarity,
    StatementTemplate,
)
from freca.logic.ast import (  # noqa: E402
    LogicOperator,
)
from freca.logic.four_valued import (  # noqa: E402
    FourValuedState,
)


# ============================================================================
# Helpers
# ============================================================================


VALID_STATES = {
    item.value
    for item in FourValuedState
}


def canonical_json(
    value: Any,
) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    )


def sha256_json(
    value: Any,
) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            canonical_json(
                value
            ).encode(
                "utf-8"
            )
        ).hexdigest()
    )


def load_json(
    path: Path,
) -> dict:
    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def save_json(
    value: Any,
    path: Path,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def state_from_pair(
    support: bool,
    attack: bool,
) -> FourValuedState:
    return FourValuedState.from_pair(
        support,
        attack,
    )


def as_state(
    value: str | FourValuedState | None,
) -> FourValuedState:
    if isinstance(
        value,
        FourValuedState,
    ):
        return value

    if value in VALID_STATES:
        return FourValuedState(
            value
        )

    return FourValuedState.UNKNOWN


def model_dump(
    model: Any,
) -> dict:
    return model.model_dump(
        mode="json"
    )


# ============================================================================
# Evidence-blind minimal template compilation
# ============================================================================


def compile_minimal_argument_template(
    *,
    contract_bundle: dict,
    evidence_requirement_plan: dict,
) -> dict:
    # FRECA MULTI-ATOM ARGUMENT OVERRIDE V1
    import multi_atom_argument_support_v1 as _freca_multi_argument_v1
    return _freca_multi_argument_v1.compile_argument_template(
        globals(),
        contract_bundle=contract_bundle,
        evidence_requirement_plan=evidence_requirement_plan,
    )



# ============================================================================
# Alignment -> observable requirement statement state
# ============================================================================


def build_requirement_statement_inputs(
    requirement_result: dict,
) -> dict[str, dict]:
    plan = requirement_result[
        "evidence_requirement_plan"
    ]

    alignments = (
        requirement_result.get(
            "alignments",
            []
        )
    )

    proof_reports = {
        str(
            item[
                "requirement_id"
            ]
        ): item
        for item
        in requirement_result.get(
            "proof_gate",
            {}
        ).get(
            "requirement_reports",
            [],
        )
    }

    result: dict[
        str,
        dict,
    ] = {}

    for requirement in plan[
        "requirements"
    ]:
        rid = str(
            requirement[
                "requirement_id"
            ]
        )

        rows = [
            row
            for row in alignments
            if str(
                row.get(
                    "requirement_id"
                )
            )
            == rid
        ]

        direct_support = [
            row
            for row in rows
            if (
                row.get(
                    "argument_admission_channel"
                )
                == "DIRECT"
                and row.get(
                    "argument_truth_bearing"
                )
                is True
                and row.get(
                    "relation"
                )
                == "SUPPORT"
            )
        ]

        direct_attack = [
            row
            for row in rows
            if (
                row.get(
                    "argument_admission_channel"
                )
                == "DIRECT"
                and row.get(
                    "argument_truth_bearing"
                )
                is True
                and row.get(
                    "relation"
                )
                == "ATTACK"
            )
        ]

        conditional = [
            row
            for row in rows
            if row.get(
                "argument_admission_channel"
            )
            == "CONDITIONAL"
        ]

        rejected = [
            row
            for row in rows
            if row.get(
                "argument_admission_channel"
            )
            == "REJECTED"
        ]

        raw_state = state_from_pair(
            bool(
                direct_support
            ),
            bool(
                direct_attack
            ),
        )

        proof_report = (
            proof_reports.get(
                rid,
                {},
            )
        )

        accepted_state = as_state(
            proof_report.get(
                "accepted_state"
            )
        )

        result[
            rid
        ] = {
            "requirement_id":
                rid,
            "statement_id":
                f"stmt-{rid.lower()}",
            "raw_state":
                raw_state.value,
            "accepted_state":
                accepted_state.value,
            "proof_standard_status":
                proof_report.get(
                    "proof_standard_status",
                    "PENDING",
                ),
            "coverage_pass":
                bool(
                    proof_report.get(
                        "coverage_pass",
                        False,
                    )
                ),
            "direct_support_alignment_ids": [
                (
                    row.get(
                        "alignment_evidence_id"
                    )
                    or row.get(
                        "fact_candidate_id"
                    )
                    or row.get(
                        "evidence_id"
                    )
                )
                for row
                in direct_support
            ],
            "direct_attack_alignment_ids": [
                (
                    row.get(
                        "alignment_evidence_id"
                    )
                    or row.get(
                        "fact_candidate_id"
                    )
                    or row.get(
                        "evidence_id"
                    )
                )
                for row
                in direct_attack
            ],
            "conditional_alignment_ids": [
                (
                    row.get(
                        "alignment_evidence_id"
                    )
                    or row.get(
                        "fact_candidate_id"
                    )
                    or row.get(
                        "evidence_id"
                    )
                )
                for row
                in conditional
            ],
            "rejected_alignment_count":
                len(
                    rejected
                ),
        }

    return result


# ============================================================================
# Direction-aware Argument standing / Statement evaluation
# ============================================================================


def _adjust_for_required_polarity(
    state: FourValuedState,
    required_polarity: RequiredPolarity,
) -> FourValuedState:
    if (
        required_polarity
        is RequiredPolarity.POSITIVE
    ):
        return state

    support, attack = (
        state.pair
    )

    return state_from_pair(
        attack,
        support,
    )


def evaluate_argument_standing(
    argument: ArgumentTemplate,
    statement_states: dict[
        str,
        FourValuedState,
    ],
    *,
    allowed_assumption_statement_ids: set[str]
    | None = None,
) -> tuple[str, list[str]]:
    """Frozen D7.13 standing semantics.

    IN:
      ordinary premises satisfied;
      permitted assumptions satisfied by policy;
      no supported exception.

    OUT:
      ordinary premise explicitly fails, or exception is established.

    CONFLICTED:
      decisive ordinary/exception premise is BOTH.

    UNDECIDED:
      remaining UNKNOWN / unresolved assumption cases.
    """

    allowed_assumption_statement_ids = (
        allowed_assumption_statement_ids
        or set()
    )

    undecided = False

    for premise in argument.premises:
        state = statement_states.get(
            premise.statement_id,
            FourValuedState.UNKNOWN,
        )

        adjusted = (
            _adjust_for_required_polarity(
                state,
                premise.required_polarity,
            )
        )

        if (
            premise.premise_role
            is PremiseRole.EXCEPTION
        ):
            if (
                adjusted
                is FourValuedState.BOTH
            ):
                return (
                    "CONFLICTED",
                    [
                        "EXCEPTION_PREMISE_BOTH",
                        premise.statement_id,
                    ],
                )

            if (
                adjusted
                is FourValuedState.TRUE
            ):
                return (
                    "OUT",
                    [
                        "EXCEPTION_PREMISE_ESTABLISHED",
                        premise.statement_id,
                    ],
                )

            if (
                adjusted
                is FourValuedState.UNKNOWN
            ):
                undecided = True

            # FALSE means the exception has been attacked/disproved.
            continue

        if (
            premise.premise_role
            is PremiseRole.ASSUMPTION
        ):
            if (
                premise.statement_id
                not in allowed_assumption_statement_ids
            ):
                undecided = True
                continue

        if (
            adjusted
            is FourValuedState.BOTH
        ):
            return (
                "CONFLICTED",
                [
                    "ORDINARY_PREMISE_BOTH",
                    premise.statement_id,
                ],
            )

        if (
            adjusted
            is FourValuedState.FALSE
        ):
            return (
                "OUT",
                [
                    "ORDINARY_PREMISE_FAILED",
                    premise.statement_id,
                ],
            )

        if (
            adjusted
            is FourValuedState.UNKNOWN
        ):
            undecided = True

    if undecided:
        return (
            "UNDECIDED",
            [
                "PREMISE_UNKNOWN_OR_ASSUMPTION_UNRESOLVED"
            ],
        )

    return (
        "IN",
        [
            "ALL_PREMISES_STANDING"
        ],
    )


def evaluate_benchmark_statement(
    *,
    template: dict,
    requirement_states: dict,
) -> dict:
    import multi_atom_argument_support_v1 as _freca_multi_argument_v1
    return _freca_multi_argument_v1.evaluate_argument_template(
        globals(),
        template=template,
        requirement_states=requirement_states,
    )



# ============================================================================
# End-to-end diagnostic
# ============================================================================


def run_argument_substrate(
    *,
    requirement_result: dict,
    contract_bundle: dict,
) -> dict:
    template = (
        compile_minimal_argument_template(
            contract_bundle=
                contract_bundle,
            evidence_requirement_plan=
                requirement_result[
                    "evidence_requirement_plan"
                ],
        )
    )

    inputs = (
        build_requirement_statement_inputs(
            requirement_result
        )
    )

    raw_states = {
        rid: as_state(
            item[
                "raw_state"
            ]
        )
        for rid, item
        in inputs.items()
    }

    accepted_states = {
        rid: as_state(
            item[
                "accepted_state"
            ]
        )
        for rid, item
        in inputs.items()
    }

    raw_shadow = (
        evaluate_benchmark_statement(
            template=
                template,
            requirement_states=
                raw_states,
        )
    )

    accepted_eval = (
        evaluate_benchmark_statement(
            template=
                template,
            requirement_states=
                accepted_states,
        )
    )

    result = {
        "schema":
            "freca-core-argument-substrate-v1",
        "cp_id":
            template[
                "cp_id"
            ],
        "reference_core_src":
            str(
                REFERENCE_CORE_SRC
            ),
        "template":
            template,
        "requirement_statement_inputs":
            inputs,

        # Diagnostic only.  It deliberately bypasses ProofStandard so that
        # developers can see whether argument-direction propagation itself
        # behaves correctly.
        "raw_shadow_evaluation": {
            "diagnostic_only":
                True,
            "uses_requirement_raw_state":
                True,
            "must_not_drive_final_outcome":
                True,
            **raw_shadow,
        },

        # Production-side argument input.  This consumes ProofStandard
        # accepted_state, not raw evidence state.
        "accepted_argument_evaluation": {
            "diagnostic_only":
                False,
            "uses_requirement_accepted_state":
                True,
            **accepted_eval,
        },

        "proof_standard_status":
            "PENDING",
        "coverage_complete":
            bool(
                requirement_result.get(
                    "proof_gate",
                    {}
                ).get(
                    "coverage_complete",
                    False,
                )
            ),
        "internal_outcome":
            "UNKNOWN",
        "submission_label":
            None,
        "evaluation_locked":
            False,
        "reason_codes": [
            "PROOF_STANDARD_PENDING",
            "NO_FINAL_LABEL_AT_ARGUMENT_SUBSTRATE",
        ],
    }

    result[
        "result_sha256"
    ] = sha256_json(
        result
    )

    return result


# ============================================================================
# Self-tests
# ============================================================================


def _fixture_template() -> dict:
    contract_bundle = {
        "contract": {
            "cp_id":
                "CPX",
            "atoms": [
                {
                    "atom_id":
                        "A1",
                    "proposition":
                        "fixture benchmark",
                }
            ],
            "satisfaction": {
                "op":
                    "ATOM",
                "atom_id":
                    "A1",
            },
        }
    }

    plan = {
        "cp_id":
            "CPX",
        "requirements": [
            {
                "requirement_id":
                    "ER1",
                "atom_id":
                    "A1",
            },
            {
                "requirement_id":
                    "ER2",
                "atom_id":
                    "A1",
            },
        ],
    }

    return (
        compile_minimal_argument_template(
            contract_bundle=
                contract_bundle,
            evidence_requirement_plan=
                plan,
        )
    )


def run_self_tests() -> None:
    template = (
        _fixture_template()
    )

    def ev(
        er1: str,
        er2: str,
    ) -> dict:
        return (
            evaluate_benchmark_statement(
                template=
                    template,
                requirement_states={
                    "ER1":
                        as_state(
                            er1
                        ),
                    "ER2":
                        as_state(
                            er2
                        ),
                },
            )
        )

    # Both positive benchmark facets accepted -> PRO argument stands.
    assert (
        ev(
            "TRUE",
            "TRUE",
        )[
            "state"
        ]
        == "TRUE"
    )

    # A proven negative ER produces an IN CON argument.
    assert (
        ev(
            "FALSE",
            "TRUE",
        )[
            "state"
        ]
        == "FALSE"
    )

    assert (
        ev(
            "TRUE",
            "FALSE",
        )[
            "state"
        ]
        == "FALSE"
    )

    # Missing/unknown proof cannot be converted into either side.
    assert (
        ev(
            "UNKNOWN",
            "TRUE",
        )[
            "state"
        ]
        == "UNKNOWN"
    )

    # BOTH is not silently voted or collapsed; the relevant arguments conflict.
    both = ev(
        "BOTH",
        "TRUE",
    )

    assert (
        both[
            "state"
        ]
        == "UNKNOWN"
    )

    assert (
        both[
            "conflicted_argument_ids"
        ]
    )

    print(
        "argument_core_v1 self-tests: PASS"
    )
    print(
        "  TRUE + TRUE     -> TRUE"
    )
    print(
        "  FALSE + TRUE    -> FALSE"
    )
    print(
        "  TRUE + FALSE    -> FALSE"
    )
    print(
        "  UNKNOWN + TRUE  -> UNKNOWN"
    )
    print(
        "  BOTH + TRUE     -> UNKNOWN + conflicted arguments"
    )


# ============================================================================
# CLI
# ============================================================================


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--requirement-result",
        type=Path,
    )

    parser.add_argument(
        "--contract",
        type=Path,
    )

    parser.add_argument(
        "--output",
        type=Path,
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:
        run_self_tests()

        if (
            args.requirement_result
            is None
            and args.contract
            is None
        ):
            return

    if (
        args.requirement_result
        is None
        or args.contract
        is None
    ):
        parser.error(
            "--requirement-result and --contract are required "
            "unless only --self-test is used"
        )

    requirement_result = load_json(
        args.requirement_result
    )

    contract_bundle = load_json(
        args.contract
    )

    result = run_argument_substrate(
        requirement_result=
            requirement_result,
        contract_bundle=
            contract_bundle,
    )

    output = (
        args.output
        or args.requirement_result.with_name(
            args.requirement_result.stem
            + "_argument_v1.json"
        )
    )

    save_json(
        result,
        output,
    )

    print(
        "=" * 72
    )
    print(
        "FRECA MINIMAL ARGUMENT SUBSTRATE V1"
    )
    print(
        "=" * 72
    )

    for rid, item in (
        result[
            "requirement_statement_inputs"
        ].items()
    ):
        print()
        print(
            rid,
            "raw=",
            item[
                "raw_state"
            ],
            "accepted=",
            item[
                "accepted_state"
            ],
            "conditional=",
            len(
                item[
                    "conditional_alignment_ids"
                ]
            ),
        )

    print()
    print(
        "Raw argument shadow :",
        result[
            "raw_shadow_evaluation"
        ][
            "state"
        ],
        "(DIAGNOSTIC ONLY)",
    )

    print(
        "Accepted argument    :",
        result[
            "accepted_argument_evaluation"
        ][
            "state"
        ],
    )

    print(
        "Final outcome        : UNKNOWN"
    )

    print(
        "Saved               :",
        output,
    )


if __name__ == "__main__":
    main()
