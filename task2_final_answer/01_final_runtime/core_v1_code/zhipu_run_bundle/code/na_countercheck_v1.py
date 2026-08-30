#!/usr/bin/env python3
"""The N/A countercheck the fold gate requires. No dependencies, no API.

WHY THIS IS ITS OWN MODULE
--------------------------
`production_runner_v1` cannot be imported without the reference core, which is
absent from the code archive by design. Keeping this function beside the runner
would mean the N/A reachability check could only run on the machine that runs
the pipeline, which is precisely where a check is least useful: it should be
possible to establish that the third label is reachable without standing up the
whole system.

WHAT IT IS FOR
--------------
`core_outcome_adapter_v1.build_fold_gate_report` accepts an `na_countercheck`
and leaves `na_countercheck_passed` False without one. The runner never passed
one, so the adapter's "positive N/A plus explicit countercheck" branch could
not be taken, and a coordinate whose upstream reasoning concluded
non-applicability fell through to UNKNOWN and folded to "0":

    without countercheck   UNKNOWN                 -> "0"   UNKNOWN_BENCHMARK_FALLBACK
    with countercheck      PROVEN_NOT_APPLICABLE   -> "N/A" RULE_FIXED_NA

One of the three permitted labels was therefore unreachable.

WHAT THE CHECK ACTUALLY IS
--------------------------
Only state the pipeline already derived: no new model call and no new prompt
text, which also keeps it clear of the rule against encoding checking-point
logic into prompts. It adds two conditions to the upstream conclusion, that
applicability must not also stand and that no violation may be standing. It is
not an independent re-derivation of non-applicability, and must not be
described as one.

WHY IT IS OFF BY DEFAULT
------------------------
Repairing the dead branch is not the same as improving the answers, and the
evidence available does not support enabling it blind.

The 615 human-labelled cells contain no N/A at all: 164 blind cells split
67/97 between 1 and 0, and 451 assisted cells split 184/267. N/A was on the
form -- the verdict column reads "HUMAN_verdict (1/0/N-A)" -- and was chosen
zero times. The pay-off is therefore asymmetric on the evidence in hand: every
coordinate that flips 0 to N/A is a certain loss against those labels, while
the gain depends on a rate no measurement here can establish. An earlier N/A
probe reached the same wall and recorded it plainly: the gold contains no N/A,
so trigger behaviour can be counted but accuracy cannot.

Leaving it off also keeps a run comparable with every result produced before,
which a silent semantic change would invalidate.

Set FRECA_ENABLE_NA_COUNTERCHECK=1 to turn it on. Do that as a measured
decision -- count how many coordinates move and which checking points they sit
on -- not as a default.
"""

from __future__ import annotations

import os

ENABLE_ENV = "FRECA_ENABLE_NA_COUNTERCHECK"


def derive_na_countercheck(root_states: dict | None) -> dict | None:
    """Return the countercheck for these root states, or None to withhold it.

    Withheld unless FRECA_ENABLE_NA_COUNTERCHECK=1, in which case no coordinate
    can be labelled N/A. See the module docstring for why that is the default.
    """
    if os.environ.get(ENABLE_ENV) != "1":
        return None

    roots = root_states or {}
    if not roots:
        return None

    non_applicable = roots.get("non_applicability_state") == "TRUE"
    applicable = roots.get("applicability_state") == "TRUE"
    violation = roots.get("violation_state") == "TRUE"

    return {
        "passed": bool(non_applicable and not applicable and not violation),
        "activity_counterevidence_standing": bool(violation),
        "countercheck_basis": "DERIVED_FROM_ROOT_STATES_V1",
    }
