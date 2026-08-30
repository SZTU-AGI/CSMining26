#!/usr/bin/env python3
"""Install FRECA observed-shape multi-atom + recursive ALL support v1.

Patches three modules, with backups and idempotent markers:
- evidence_reasoning_v2.py: multi-atom EvidenceRequirement planning/validation
- argument_core_v1.py: multi-atom legal-atom projection + nested ALL evaluation
- core_outcome_adapter_v1.py: recursive ALL root projection from ProofStandard

Observed operators only: CONST / ATOM / ALL.
No case evidence, final labels, or answer comparator are introduced.
"""

from __future__ import annotations

import argparse
import ast
import shutil
from pathlib import Path

import multi_atom_support_v1 as support
import multi_atom_argument_support_v1 as argument_support

ER_MARKER = "FRECA MULTI-ATOM EVIDENCE OVERRIDE V1"
ARG_MARKER = "FRECA MULTI-ATOM ARGUMENT OVERRIDE V1"
AD_MARKER = "FRECA RECURSIVE ALL ADAPTER OVERRIDE V1"

ER_OVERRIDE = r'''
# FRECA MULTI-ATOM EVIDENCE OVERRIDE V1
import multi_atom_support_v1 as _freca_multi_atom_v1
EVIDENCE_REQUIREMENT_SYSTEM = _freca_multi_atom_v1.MULTI_ATOM_EVIDENCE_SYSTEM


def make_evidence_requirement_prompt(
    cp: dict,
    contract: dict,
    ledger_artifact: dict,
    rule_set_relation: dict,
) -> str:
    return _freca_multi_atom_v1.make_evidence_requirement_prompt(
        globals(), cp, contract, ledger_artifact, rule_set_relation
    )


def validate_evidence_requirements(
    raw: dict,
    cp: dict,
    contract: dict,
    ledger_artifact: dict,
    rule_set_relation: dict,
) -> dict:
    return _freca_multi_atom_v1.validate_evidence_requirements(
        globals(), raw, cp, contract, ledger_artifact, rule_set_relation
    )
'''

ARG_COMPILE = r'''def compile_minimal_argument_template(
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
'''

ARG_EVAL = r'''def evaluate_benchmark_statement(
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
'''

AD_DERIVE = r'''def derive_root_states(
    *,
    contract_bundle: dict,
    proof_bundle: dict,
    requirement_result: dict,
) -> dict:
    # FRECA RECURSIVE ALL ADAPTER OVERRIDE V1
    import multi_atom_support_v1 as _freca_multi_atom_v1
    return _freca_multi_atom_v1.derive_root_states(
        globals(),
        contract_bundle=contract_bundle,
        proof_bundle=proof_bundle,
        requirement_result=requirement_result,
    )
'''


def top_level_function(source: str, name: str):
    tree = ast.parse(source)
    matches = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {name}; found {len(matches)}")
    return matches[0]


def replace_function(source: str, name: str, replacement: str) -> str:
    node = top_level_function(source, name)
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [replacement.rstrip() + "\n\n"]
    return "".join(lines)


def insert_before_function(source: str, name: str, block: str) -> str:
    node = top_level_function(source, name)
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.lineno - 1] = [block.rstrip() + "\n\n"]
    return "".join(lines)


def patch_evidence(source: str) -> str:
    if ER_MARKER in source:
        return source
    if "def compile_evidence_requirements(" not in source:
        raise RuntimeError("Missing compile_evidence_requirements")
    patched = insert_before_function(
        source, "compile_evidence_requirements", ER_OVERRIDE
    )
    ast.parse(patched)
    return patched


def patch_argument(source: str) -> str:
    if ARG_MARKER in source:
        return source
    patched = replace_function(
        source, "compile_minimal_argument_template", ARG_COMPILE
    )
    patched = replace_function(
        patched, "evaluate_benchmark_statement", ARG_EVAL
    )
    if ARG_MARKER not in patched:
        raise RuntimeError("Argument marker missing after patch")
    ast.parse(patched)
    return patched


def patch_adapter(source: str) -> str:
    if AD_MARKER in source:
        return source
    patched = replace_function(source, "derive_root_states", AD_DERIVE)
    old = '''roots = derive_root_states(
        contract_bundle=contract_bundle,
        proof_bundle=proof_bundle,
    )'''
    new = '''roots = derive_root_states(
        contract_bundle=contract_bundle,
        proof_bundle=proof_bundle,
        requirement_result=requirement_result,
    )'''
    if old not in patched:
        raise RuntimeError("Could not locate derive_root_states call site")
    patched = patched.replace(old, new, 1)
    ast.parse(patched)
    return patched


def run_self_test(evidence: Path, argument: Path, adapter: Path) -> None:
    support.self_test()
    argument_support.self_test()
    ev = patch_evidence(evidence.read_text(encoding="utf-8"))
    ar = patch_argument(argument.read_text(encoding="utf-8"))
    ad = patch_adapter(adapter.read_text(encoding="utf-8"))
    ast.parse(ev)
    ast.parse(ar)
    ast.parse(ad)
    assert ER_MARKER in ev
    assert ARG_MARKER in ar
    assert AD_MARKER in ad
    assert patch_evidence(ev) == ev
    assert patch_argument(ar) == ar
    assert patch_adapter(ad) == ad
    print("install_multi_atom_all_v1 self-tests: PASS")
    print("  evidence / argument / adapter target source parses")
    print("  idempotent markers")
    print("  observed operators only: CONST / ATOM / ALL")


def _install_one(path: Path, new_source: str, backup_suffix: str) -> Path:
    backup = path.with_name(path.name + backup_suffix)
    if not backup.exists():
        shutil.copy2(path, backup)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new_source, encoding="utf-8")
    ast.parse(tmp.read_text(encoding="utf-8"))
    tmp.replace(path)
    return backup


def install(evidence: Path, argument: Path, adapter: Path) -> None:
    ev_new = patch_evidence(evidence.read_text(encoding="utf-8"))
    ar_new = patch_argument(argument.read_text(encoding="utf-8"))
    ad_new = patch_adapter(adapter.read_text(encoding="utf-8"))
    ast.parse(ev_new)
    ast.parse(ar_new)
    ast.parse(ad_new)
    support.self_test()
    argument_support.self_test()

    backups = [
        _install_one(evidence, ev_new, ".before_multi_atom_all_v1"),
        _install_one(argument, ar_new, ".before_multi_atom_all_v1"),
        _install_one(adapter, ad_new, ".before_multi_atom_all_v1"),
    ]

    print("Installed multi-atom / recursive-ALL v1")
    print(" evidence:", evidence)
    print(" argument:", argument)
    print(" adapter :", adapter)
    print(" backups:")
    for path in backups:
        print("  ", path)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--evidence", type=Path, default=Path("evidence_reasoning_v2.py"))
    p.add_argument("--argument", type=Path, default=Path("argument_core_v1.py"))
    p.add_argument("--adapter", type=Path, default=Path("core_outcome_adapter_v1.py"))
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--install", action="store_true")
    args = p.parse_args()

    for path in (args.evidence, args.argument, args.adapter):
        if not path.exists():
            p.error(f"Missing {path}")

    if args.self_test:
        run_self_test(args.evidence, args.argument, args.adapter)
    if args.install:
        install(args.evidence, args.argument, args.adapter)
    if not args.self_test and not args.install:
        p.error("choose --self-test and/or --install")


if __name__ == "__main__":
    main()
