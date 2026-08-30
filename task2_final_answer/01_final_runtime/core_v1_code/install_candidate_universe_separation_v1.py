#!/usr/bin/env python3
"""Install FRECA candidate-universe / model-context separation v1.

Patches:
  evidence_reasoning_v2.py:
    retrieve_requirement_candidates

  identity_admissibility_v1.py:
    apply_identity_gate_to_traces

  coverage_v1.py:
    candidate_disposition
    structure_channel
    evaluate_need_coverage

Does NOT patch:
  FactCandidate
  alignment prompt/validator
  Argument
  ProofStandard
  final fold
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

EVIDENCE = Path("evidence_reasoning_v2.py")
IDENTITY = Path("identity_admissibility_v1.py")
COVERAGE = Path("coverage_v1.py")

NEW_RETRIEVE = '\ndef retrieve_requirement_candidates(\n    evidence_chunks: list[dict],\n    needs: list[dict],\n    *,\n    top_k: int = 12,\n) -> list[dict]:\n    """Retrieve with candidate-universe / model-context separation.\n\n    Frozen semantics:\n      - lexical full scan: persist every strictly-positive BM25 hit;\n      - typed scan: full-case deterministic scan for BOTH directions;\n      - structure: neighbour rescue only for now (explicitly not full coverage);\n      - candidate_universe is never truncated by model context;\n      - candidates remains the backward-compatible context-packed subset.\n    """\n    from evidence_nature_v1 import (\n        classify_evidence_nature,\n        infer_requirement_predicate_profile,\n    )\n\n    docs = []\n    by_id = {}\n\n    for chunk in evidence_chunks:\n        cid = chunk_id(chunk)\n        text = chunk_text(chunk)\n\n        item = dict(chunk)\n        item["id"] = cid\n        item["text"] = text\n\n        docs.append(item)\n        by_id[cid] = item\n\n    # Legacy ranking budget retained ONLY for context ordering.\n    context_ranking_limit = 40\n    rrf_k = 60\n    per_variant_quota = 3\n    support_context_cap = max(24, top_k)\n    attack_context_cap = max(24, top_k)\n\n    full_rank_cache = {}\n\n    def full_positive_rank(\n        query: str,\n    ) -> list[dict]:\n        query = normalize_ws(query)\n\n        if not query:\n            return []\n\n        if query not in full_rank_cache:\n            ranked = core.bm25_rank(\n                query,\n                docs,\n                len(docs),\n            )\n\n            # A BM25 score of exactly zero means no lexical query-term hit.\n            # This deterministic boundary is not tuned to cases or labels.\n            full_rank_cache[query] = [\n                item\n                for item in ranked\n                if float(\n                    item.get(\n                        "score",\n                        0.0,\n                    )\n                    or 0.0\n                ) > 0.0\n            ]\n\n        return full_rank_cache[\n            query\n        ]\n\n    def neighbour_ids(\n        evidence_id: str,\n    ) -> list[str]:\n        out = []\n\n        paragraph = re.match(\n            r"^(.*):P(\\d+)$",\n            evidence_id,\n        )\n\n        if paragraph:\n            prefix = paragraph.group(1)\n            number = int(\n                paragraph.group(2)\n            )\n\n            for adjacent in (\n                number - 1,\n                number + 1,\n            ):\n                if adjacent >= 1:\n                    out.append(\n                        f"{prefix}:P{adjacent}"\n                    )\n\n            return out\n\n        row = re.match(\n            r"^(.*):R(\\d+)$",\n            evidence_id,\n        )\n\n        if row:\n            prefix = row.group(1)\n            number = int(\n                row.group(2)\n            )\n\n            for adjacent in (\n                number - 1,\n                number + 1,\n            ):\n                if adjacent >= 1:\n                    out.append(\n                        f"{prefix}:R{adjacent}"\n                    )\n\n        return out\n\n    def rrf_order(\n        rankings: list[list[str]],\n    ) -> tuple[\n        list[str],\n        dict[str, float],\n    ]:\n        totals = {}\n        best_rank = {}\n        first_seen = {}\n        counter = 0\n\n        for ranking in rankings:\n            for rank, evidence_id in enumerate(\n                ranking,\n                start=1,\n            ):\n                totals[evidence_id] = (\n                    totals.get(\n                        evidence_id,\n                        0.0,\n                    )\n                    + 1.0\n                    / (\n                        rrf_k\n                        + rank\n                    )\n                )\n\n                best_rank[evidence_id] = min(\n                    best_rank.get(\n                        evidence_id,\n                        10**9,\n                    ),\n                    rank,\n                )\n\n                if (\n                    evidence_id\n                    not in first_seen\n                ):\n                    first_seen[\n                        evidence_id\n                    ] = counter\n                    counter += 1\n\n        ordered = sorted(\n            totals,\n            key=lambda evidence_id: (\n                -totals[\n                    evidence_id\n                ],\n                best_rank[\n                    evidence_id\n                ],\n                first_seen[\n                    evidence_id\n                ],\n                evidence_id,\n            ),\n        )\n\n        return (\n            ordered,\n            totals,\n        )\n\n    typed_natures_by_target = {\n        "DESIGN_CONSTRUCTION": {\n            "PHYSICAL_DESIGN_FEATURE",\n            "DESIGN_OR_CONSTRUCTION_DEFECT",\n        },\n        "CURRENT_CONDITION": {\n            "CURRENT_CONDITION",\n            "OBSERVATION_RECORD",\n            "REVIEW_FINDING",\n            "ACTIVITY_RECORD",\n            "CURRENT_MAINTENANCE_OR_CONDITION_DEFECT",\n            "ADVERSE_OPERATIONAL_FINDING",\n            "EXPLICIT_CONTROL_ABSENCE",\n        },\n        "ACTIVITY_PERFORMED": {\n            "ACTIVITY_RECORD",\n            "OBSERVATION_RECORD",\n            "REVIEW_FINDING",\n            "PROCEDURE_STATEMENT",\n            "PLAN_STATEMENT",\n        },\n        "RECORDKEEPING": {\n            "RECORD_EXISTS",\n            "ACTIVITY_RECORD",\n            "DOCUMENTATION_GAP",\n        },\n        "DOCUMENTATION": {\n            "RECORD_EXISTS",\n            "ACTIVITY_RECORD",\n            "DOCUMENTATION_GAP",\n        },\n        "PROCEDURE_OR_PLAN_EXISTS": {\n            "PROCEDURE_STATEMENT",\n            "PLAN_STATEMENT",\n            "DOCUMENTATION_GAP",\n        },\n        "OUTCOME_STATE": {\n            "CURRENT_CONDITION",\n            "OBSERVATION_RECORD",\n            "REVIEW_FINDING",\n            "CURRENT_MAINTENANCE_OR_CONDITION_DEFECT",\n            "ADVERSE_OPERATIONAL_FINDING",\n        },\n    }\n\n    typed_cache = {}\n\n    def typed_record(\n        item: dict,\n    ) -> dict:\n        evidence_id = item[\n            "id"\n        ]\n\n        if (\n            evidence_id\n            not in typed_cache\n        ):\n            typed_cache[\n                evidence_id\n            ] = (\n                classify_evidence_nature(\n                    item[\n                        "text"\n                    ]\n                )\n            )\n\n        return typed_cache[\n            evidence_id\n        ]\n\n    def typed_candidates_for_need(\n        need: dict,\n    ) -> tuple[\n        list[str],\n        dict,\n    ]:\n        facets = need.get(\n            "query_facets",\n            [],\n        )\n\n        proposition = (\n            facets[-1]\n            if facets\n            else ""\n        )\n\n        pseudo_requirement = {\n            "proposition_to_establish":\n                proposition,\n            "query_sources":\n                [],\n        }\n\n        profile = (\n            infer_requirement_predicate_profile(\n                pseudo_requirement\n            )\n        )\n\n        target_kinds = profile.get(\n            "target_kinds",\n            [],\n        )\n\n        wanted = set()\n\n        for target_kind in target_kinds:\n            wanted.update(\n                typed_natures_by_target.get(\n                    target_kind,\n                    set(),\n                )\n            )\n\n        matched = []\n\n        if wanted:\n            for item in docs:\n                typed = typed_record(\n                    item\n                )\n\n                natures = set(\n                    typed.get(\n                        "evidence_natures",\n                        [],\n                    )\n                )\n\n                if (\n                    natures\n                    & wanted\n                ):\n                    matched.append(\n                        item[\n                            "id"\n                        ]\n                    )\n\n        return matched, {\n            "target_kinds":\n                target_kinds,\n            "wanted_natures":\n                sorted(\n                    wanted\n                ),\n            "scan_chunk_count":\n                len(docs),\n            "matched_count":\n                len(\n                    matched\n                ),\n            "full_case_scan":\n                True,\n        }\n\n    traces = []\n\n    for need in needs:\n        combined_query = _build_query(\n            need[\n                "query_facets"\n            ]\n        )\n\n        variant_rows = []\n        context_rankings = []\n        full_lexical_ids = []\n        full_lexical_seen = set()\n        lexical_best_score = {}\n\n        source_variants = list(\n            need.get(\n                "query_variants",\n                [],\n            )\n        )\n\n        if not source_variants:\n            source_variants = [\n                {\n                    "variant_id":\n                        (\n                            f"{need[\'need_id\']}"\n                            ".fallback"\n                        ),\n                    "source":\n                        "COMBINED",\n                    "candidate_id":\n                        None,\n                    "query":\n                        combined_query,\n                    "transformation":\n                        "COMBINED_FALLBACK",\n                }\n            ]\n\n        for variant in source_variants:\n            query = normalize_ws(\n                variant.get(\n                    "query",\n                    "",\n                )\n            )\n\n            if not query:\n                continue\n\n            full_ranked = (\n                full_positive_rank(\n                    query\n                )\n            )\n\n            full_ids = [\n                item[\n                    "id"\n                ]\n                for item\n                in full_ranked\n            ]\n\n            for item in full_ranked:\n                evidence_id = item[\n                    "id"\n                ]\n\n                score = float(\n                    item.get(\n                        "score",\n                        0.0,\n                    )\n                    or 0.0\n                )\n\n                lexical_best_score[\n                    evidence_id\n                ] = max(\n                    lexical_best_score.get(\n                        evidence_id,\n                        0.0,\n                    ),\n                    score,\n                )\n\n                if (\n                    evidence_id\n                    not in full_lexical_seen\n                ):\n                    full_lexical_seen.add(\n                        evidence_id\n                    )\n                    full_lexical_ids.append(\n                        evidence_id\n                    )\n\n            # Preserve the previous 40-item ranking budget only for RRF/context.\n            context_ids = full_ids[\n                :context_ranking_limit\n            ]\n\n            if context_ids:\n                context_rankings.append(\n                    context_ids\n                )\n\n            variant_rows.append(\n                {\n                    **variant,\n                    "candidate_limit":\n                        context_ranking_limit,\n                    "full_hit_count":\n                        len(\n                            full_ids\n                        ),\n                    "full_ids":\n                        full_ids,\n                    "top_ids":\n                        full_ids[\n                            :per_variant_quota\n                        ],\n                }\n            )\n\n        (\n            fused_context_ids,\n            rrf_scores,\n        ) = rrf_order(\n            context_rankings\n        )\n\n        (\n            typed_ids,\n            typed_trace,\n        ) = typed_candidates_for_need(\n            need\n        )\n\n        # ------------------------------------------------------------\n        # Complete currently-discovered candidate universe.\n        # ------------------------------------------------------------\n        universe_ids = []\n        methods_by_id = {}\n\n        def add_universe(\n            evidence_id: str,\n            method: str,\n        ) -> None:\n            if (\n                evidence_id\n                not in by_id\n            ):\n                return\n\n            methods_by_id.setdefault(\n                evidence_id,\n                [],\n            )\n\n            if (\n                method\n                not in methods_by_id[\n                    evidence_id\n                ]\n            ):\n                methods_by_id[\n                    evidence_id\n                ].append(\n                    method\n                )\n\n            if (\n                evidence_id\n                not in universe_ids\n            ):\n                universe_ids.append(\n                    evidence_id\n                )\n\n        for evidence_id in typed_ids:\n            add_universe(\n                evidence_id,\n                "TYPED_FACT_SCAN",\n            )\n\n        for evidence_id in full_lexical_ids:\n            add_universe(\n                evidence_id,\n                "LEXICAL_BM25_FULL_HIT",\n            )\n\n        # Structure is still rescue-only, but it is run across ALL currently\n        # discovered lexical/typed seeds, not merely the 24 context items.\n        structure_seed_ids = list(\n            universe_ids\n        )\n\n        for evidence_id in structure_seed_ids:\n            for neighbour_id in neighbour_ids(\n                evidence_id\n            ):\n                add_universe(\n                    neighbour_id,\n                    "STRUCTURE_NEIGHBOUR",\n                )\n\n        candidate_universe = []\n\n        for universe_rank, evidence_id in enumerate(\n            universe_ids,\n            start=1,\n        ):\n            item = by_id[\n                evidence_id\n            ]\n\n            candidate_universe.append(\n                {\n                    "evidence_id":\n                        evidence_id,\n                    "score":\n                        rrf_scores.get(\n                            evidence_id\n                        ),\n                    "lexical_best_score":\n                        lexical_best_score.get(\n                            evidence_id\n                        ),\n                    "text":\n                        item[\n                            "text"\n                        ],\n                    "retrieval_methods":\n                        methods_by_id.get(\n                            evidence_id,\n                            [],\n                        ),\n                    "universe_rank":\n                        universe_rank,\n                }\n            )\n\n        # ------------------------------------------------------------\n        # Backward-compatible model context packing.\n        # This remains bounded; it does NOT define coverage.\n        # ------------------------------------------------------------\n        context_cap = (\n            support_context_cap\n            if need[\n                "direction"\n            ]\n            == "SUPPORT"\n            else attack_context_cap\n        )\n\n        context_ids = []\n\n        def add_context(\n            evidence_id: str,\n        ) -> None:\n            if (\n                evidence_id\n                not in by_id\n            ):\n                return\n\n            if (\n                evidence_id\n                not in context_ids\n            ):\n                context_ids.append(\n                    evidence_id\n                )\n\n        # Typed candidates first for BOTH directions.\n        for evidence_id in typed_ids:\n            add_context(\n                evidence_id\n            )\n\n        # Keep frozen source-grounded variant quota.\n        for variant in variant_rows:\n            for evidence_id in variant[\n                "top_ids"\n            ]:\n                add_context(\n                    evidence_id\n                )\n\n                for neighbour_id in neighbour_ids(\n                    evidence_id\n                ):\n                    add_context(\n                        neighbour_id\n                    )\n\n        # RRF fill is ranking/context only.\n        for evidence_id in fused_context_ids:\n            if (\n                len(\n                    context_ids\n                )\n                >= context_cap\n            ):\n                break\n\n            add_context(\n                evidence_id\n            )\n\n        context_ids = context_ids[\n            :context_cap\n        ]\n\n        universe_by_id = {\n            row[\n                "evidence_id"\n            ]: row\n            for row\n            in candidate_universe\n        }\n\n        candidates = []\n\n        for context_rank, evidence_id in enumerate(\n            context_ids,\n            start=1,\n        ):\n            row = dict(\n                universe_by_id[\n                    evidence_id\n                ]\n            )\n\n            row[\n                "context_rank"\n            ] = context_rank\n\n            row[\n                "selected_for_model_context"\n            ] = True\n\n            candidates.append(\n                row\n            )\n\n        traces.append(\n            {\n                **need,\n                "query":\n                    combined_query,\n                "query_plan_mode":\n                    (\n                        "CANDIDATE_UNIVERSE_"\n                        "PLUS_CONTEXT_PACKING_V1"\n                    ),\n                "query_variants":\n                    variant_rows,\n                "typed_fact_scan":\n                    typed_trace,\n\n                # Full-discovery ledger.\n                "candidate_universe_persisted":\n                    True,\n                "candidate_universe_ids":\n                    universe_ids,\n                "candidate_universe_count":\n                    len(\n                        universe_ids\n                    ),\n                "candidate_universe":\n                    candidate_universe,\n\n                # Explicit context ledger.\n                "model_context_candidate_ids":\n                    context_ids,\n                "model_context_count":\n                    len(\n                        context_ids\n                    ),\n                "model_context_cap":\n                    context_cap,\n\n                # Backward-compatible fields used by alignment.\n                "candidates":\n                    candidates,\n                "candidate_count_checked_by_model":\n                    len(\n                        candidates\n                    ),\n\n                # Legacy ranking budget is NOT a coverage boundary.\n                "candidate_limit":\n                    context_ranking_limit,\n                "context_ranking_limit":\n                    context_ranking_limit,\n                "per_variant_quota":\n                    per_variant_quota,\n                (\n                    "support_context_cap"\n                    if need[\n                        "direction"\n                    ]\n                    == "SUPPORT"\n                    else "attack_context_cap"\n                ):\n                    context_cap,\n\n                "retrieval_scan_chunk_count":\n                    len(docs),\n\n                "structure_scan":\n                    {\n                        "mode":\n                            "SEED_NEIGHBOUR_RESCUE_ONLY",\n                        "scan_chunk_count":\n                            len(docs),\n                        "seed_count":\n                            len(\n                                structure_seed_ids\n                            ),\n                        "matched_count":\n                            len(\n                                [\n                                    x\n                                    for x\n                                    in universe_ids\n                                    if (\n                                        "STRUCTURE_NEIGHBOUR"\n                                        in methods_by_id.get(\n                                            x,\n                                            [],\n                                        )\n                                    )\n                                ]\n                            ),\n                        "full_scan":\n                            False,\n                    },\n\n                "coverage_status":\n                    (\n                        "CANDIDATE_UNIVERSE_"\n                        "PRESERVED_CONTEXT_PACKED_V1"\n                    ),\n            }\n        )\n\n    return traces\n'
NEW_IDENTITY = '\ndef apply_identity_gate_to_traces(\n    traces: list[dict[str, Any]],\n    identity_report: dict[str, Any],\n) -> list[dict[str, Any]]:\n    """Annotate the full retrieval universe, then mirror annotations to context.\n\n    Nothing is deleted.  The candidate universe is the audit/coverage ledger;\n    ``candidates`` is only the context-packed subset used by the current\n    semantic alignment adapter.\n    """\n\n    out = copy.deepcopy(\n        traces\n    )\n\n    source_relations = (\n        identity_report.get(\n            "source_relations"\n        )\n        or {}\n    )\n\n    def annotate(\n        candidate: dict[str, Any],\n    ) -> None:\n        source_id = (\n            _candidate_source_id(\n                candidate\n            )\n        )\n\n        source = (\n            source_relations.get(\n                source_id,\n                {\n                    "relation_to_case":\n                        "CASE_ASSOCIATED_OWNER_UNKNOWN",\n                    "status":\n                        "CONDITIONAL",\n                },\n            )\n        )\n\n        relation = (\n            _chunk_owner_relation(\n                str(\n                    candidate.get(\n                        "text"\n                    )\n                    or ""\n                ),\n                str(\n                    source.get(\n                        "relation_to_case"\n                    )\n                    or "UNKNOWN"\n                ),\n                identity_report,\n            )\n        )\n\n        (\n            decision,\n            decisive_ok,\n            reason,\n        ) = _use_decision(\n            relation,\n            identity_report,\n        )\n\n        candidate[\n            "source_id"\n        ] = source_id\n\n        candidate[\n            "identity_relation_to_case"\n        ] = relation\n\n        candidate[\n            "identity_use_decision"\n        ] = decision\n\n        candidate[\n            "identity_decisive_proof_eligible"\n        ] = decisive_ok\n\n        candidate[\n            "identity_reason_code"\n        ] = reason\n\n    for trace in out:\n        universe = trace.get(\n            "candidate_universe"\n        )\n\n        if not isinstance(\n            universe,\n            list,\n        ):\n            universe = trace.get(\n                "candidates",\n                [],\n            )\n\n        direct = 0\n        conditional = 0\n        excluded = 0\n\n        for candidate in universe:\n            annotate(\n                candidate\n            )\n\n            decision = candidate.get(\n                "identity_use_decision"\n            )\n\n            if (\n                decision\n                == "ADMIT_DIRECT"\n            ):\n                direct += 1\n\n            elif (\n                decision\n                == "EXCLUDE_SUBSTANTIVE"\n            ):\n                excluded += 1\n\n            else:\n                conditional += 1\n\n        # Rebuild the context subset from the already-annotated universe so the\n        # same evidence ID cannot receive two different identity decisions.\n        by_id = {\n            str(\n                candidate.get(\n                    "evidence_id"\n                )\n            ): candidate\n            for candidate\n            in universe\n        }\n\n        old_context = trace.get(\n            "candidates",\n            [],\n        )\n\n        rebuilt_context = []\n\n        for context_candidate in old_context:\n            evidence_id = str(\n                context_candidate.get(\n                    "evidence_id"\n                )\n            )\n\n            base = by_id.get(\n                evidence_id\n            )\n\n            if base is None:\n                # Backward-compatible defensive path.\n                copy_candidate = (\n                    copy.deepcopy(\n                        context_candidate\n                    )\n                )\n\n                annotate(\n                    copy_candidate\n                )\n\n                rebuilt_context.append(\n                    copy_candidate\n                )\n                continue\n\n            copy_candidate = (\n                copy.deepcopy(\n                    base\n                )\n            )\n\n            # Preserve context-only metadata.\n            for key in (\n                "context_rank",\n                "selected_for_model_context",\n            ):\n                if key in context_candidate:\n                    copy_candidate[\n                        key\n                    ] = context_candidate[\n                        key\n                    ]\n\n            rebuilt_context.append(\n                copy_candidate\n            )\n\n        trace[\n            "candidate_universe"\n        ] = universe\n\n        trace[\n            "candidates"\n        ] = rebuilt_context\n\n        trace[\n            "identity_gate_summary"\n        ] = {\n            "universe_count":\n                len(\n                    universe\n                ),\n            "admit_direct":\n                direct,\n            "admit_conditional":\n                conditional,\n            "exclude_substantive":\n                excluded,\n            "summary_scope":\n                "CANDIDATE_UNIVERSE",\n        }\n\n    return out\n'
NEW_CANDIDATE_DISPOSITION = '\ndef candidate_disposition(\n    *,\n    trace: dict,\n    alignments: list[dict],\n) -> dict:\n    """Disposition over the FULL candidate universe, not only model context."""\n\n    need_id = str(\n        trace.get(\n            "need_id",\n            "",\n        )\n    )\n\n    universe = trace.get(\n        "candidate_universe"\n    )\n\n    if not isinstance(\n        universe,\n        list,\n    ):\n        universe = trace.get(\n            "candidates",\n            [],\n        )\n\n    context = trace.get(\n        "candidates",\n        [],\n    )\n\n    rows_for_need = [\n        row\n        for row in alignments\n        if need_id\n        in (\n            row.get(\n                "retrieval_need_ids",\n                [],\n            )\n            or []\n        )\n    ]\n\n    rows_by_parent = {}\n\n    for row in rows_for_need:\n        parent = str(\n            row.get(\n                "evidence_id",\n                "",\n            )\n        )\n\n        rows_by_parent.setdefault(\n            parent,\n            [],\n        ).append(\n            row\n        )\n\n    assessed = []\n    excluded = []\n    conditional = []\n    unassessed = []\n\n    for candidate in universe:\n        evidence_id = str(\n            candidate.get(\n                "evidence_id",\n                "",\n            )\n        )\n\n        use = str(\n            candidate.get(\n                "identity_use_decision",\n                "",\n            )\n        )\n\n        # Deterministic Layer-5 dispositions are fully assessed without model\n        # semantic alignment.\n        if use in {\n            "EXCLUDE_SUBSTANTIVE",\n            "CONTEXT_ONLY",\n            "GAP_SIGNAL_ONLY",\n        }:\n            assessed.append(\n                evidence_id\n            )\n            excluded.append(\n                evidence_id\n            )\n            continue\n\n        rows = rows_by_parent.get(\n            evidence_id,\n            [],\n        )\n\n        if rows:\n            assessed.append(\n                evidence_id\n            )\n\n            if (\n                use\n                == "ADMIT_CONDITIONAL"\n                or any(\n                    row.get(\n                        "argument_admission_channel"\n                    )\n                    == "CONDITIONAL"\n                    for row\n                    in rows\n                )\n            ):\n                conditional.append(\n                    evidence_id\n                )\n\n            continue\n\n        # ADMIT_DIRECT/ADMIT_CONDITIONAL is only an identity/use decision.\n        # Without a semantic relation disposition, the candidate remains\n        # unassessed for RequirementCoverage.\n        unassessed.append(\n            evidence_id\n        )\n\n        if (\n            use\n            == "ADMIT_CONDITIONAL"\n        ):\n            conditional.append(\n                evidence_id\n            )\n\n    return {\n        "candidate_universe_count":\n            len(\n                universe\n            ),\n        "context_selected_count":\n            len(\n                context\n            ),\n        "universe_assessed_count":\n            len(\n                assessed\n            ),\n        "universe_unassessed_candidate_ids":\n            sorted(\n                set(\n                    unassessed\n                )\n            ),\n        "universe_excluded_candidate_ids":\n            sorted(\n                set(\n                    excluded\n                )\n            ),\n        "universe_conditional_candidate_ids":\n            sorted(\n                set(\n                    conditional\n                )\n            ),\n        "alignment_ids":\n            [\n                alignment_id(\n                    row\n                )\n                for row\n                in rows_for_need\n            ],\n        "model_or_rule_assessed_parent_ids":\n            sorted(\n                rows_by_parent\n            ),\n\n        # Backward-compatible aliases used by the v1 report builder.\n        "selected_candidate_count":\n            len(\n                universe\n            ),\n        "selected_assessed_count":\n            len(\n                assessed\n            ),\n        "selected_unassessed_candidate_ids":\n            sorted(\n                set(\n                    unassessed\n                )\n            ),\n        "selected_excluded_candidate_ids":\n            sorted(\n                set(\n                    excluded\n                )\n            ),\n        "selected_conditional_candidate_ids":\n            sorted(\n                set(\n                    conditional\n                )\n            ),\n    }\n'
NEW_STRUCTURE_CHANNEL = '\ndef structure_channel(\n    trace: dict,\n) -> dict:\n    universe = trace.get(\n        "candidate_universe"\n    )\n\n    if not isinstance(\n        universe,\n        list,\n    ):\n        universe = trace.get(\n            "candidates",\n            [],\n        )\n\n    neighbour_hits = [\n        candidate\n        for candidate\n        in universe\n        if (\n            "STRUCTURE_NEIGHBOUR"\n            in candidate.get(\n                "retrieval_methods",\n                [],\n            )\n        )\n    ]\n\n    structure_scan = (\n        trace.get(\n            "structure_scan"\n        )\n        or {}\n    )\n\n    explicit_full = bool(\n        trace.get(\n            "structure_full_scan"\n        )\n        or trace.get(\n            "structure_scan_complete"\n        )\n        or structure_scan.get(\n            "full_scan"\n        )\n    )\n\n    if explicit_full:\n        mode = (\n            "FULL_STRUCTURE_SCAN"\n        )\n\n    elif neighbour_hits:\n        mode = (\n            "SEED_NEIGHBOUR_RESCUE_ONLY"\n        )\n\n    else:\n        mode = (\n            "NOT_EXECUTED"\n        )\n\n    return {\n        "channel":\n            "STRUCTURE",\n        "executed":\n            bool(\n                explicit_full\n                or neighbour_hits\n            ),\n        "mode":\n            mode,\n        "neighbour_candidate_count":\n            len(\n                neighbour_hits\n            ),\n        "scan_chunk_count":\n            int(\n                structure_scan.get(\n                    "scan_chunk_count",\n                    0,\n                )\n                or 0\n            ),\n        "candidate_universe_preserved":\n            bool(\n                trace.get(\n                    "candidate_universe_persisted",\n                    False,\n                )\n            ),\n        "complete_for_population_coverage":\n            explicit_full,\n    }\n'
NEW_EVALUATE_NEED = '\ndef evaluate_need_coverage(\n    *,\n    trace: dict,\n    alignments: list[dict],\n) -> dict:\n    need_id = str(\n        trace.get(\n            "need_id",\n            "",\n        )\n    )\n\n    requirement_id = str(\n        trace.get(\n            "requirement_id",\n            "",\n        )\n    )\n\n    direction = str(\n        trace.get(\n            "direction",\n            "",\n        )\n    )\n\n    lexical = lexical_channel(\n        trace\n    )\n\n    typed = typed_channel(\n        trace\n    )\n\n    structure = structure_channel(\n        trace\n    )\n\n    channels = {\n        item[\n            "channel"\n        ]: item\n        for item\n        in (\n            lexical,\n            typed,\n            structure,\n        )\n    }\n\n    disposition = (\n        candidate_disposition(\n            trace=\n                trace,\n            alignments=\n                alignments,\n        )\n    )\n\n    limiting_factors = []\n    failed_channels = []\n    executed_channels = []\n\n    for channel_name in (\n        REQUIRED_CHANNELS\n    ):\n        channel = channels[\n            channel_name\n        ]\n\n        if channel[\n            "executed"\n        ]:\n            executed_channels.append(\n                channel_name\n            )\n\n        if not channel[\n            "complete_for_population_coverage"\n        ]:\n            failed_channels.append(\n                channel_name\n            )\n\n            if (\n                channel_name\n                == "RAW_LEXICAL"\n            ):\n                limiting_factors.append(\n                    "RAW_LEXICAL_CANDIDATE_UNIVERSE_NOT_PERSISTED"\n                )\n\n            elif (\n                channel_name\n                == "TYPED_FACT"\n            ):\n                limiting_factors.append(\n                    "TYPED_FACT_REQUIRED_CHANNEL_NOT_COMPLETE"\n                )\n\n            elif (\n                channel_name\n                == "STRUCTURE"\n            ):\n                limiting_factors.append(\n                    "STRUCTURE_FULL_SCAN_NOT_EXECUTED"\n                )\n\n    candidate_universe_preserved = bool(\n        trace.get(\n            "candidate_universe_persisted",\n            False,\n        )\n    )\n\n    if not candidate_universe_preserved:\n        limiting_factors.append(\n            "CANDIDATE_UNIVERSE_NOT_PERSISTED"\n        )\n\n    unassessed = disposition[\n        "universe_unassessed_candidate_ids"\n    ]\n\n    if unassessed:\n        limiting_factors.append(\n            "CANDIDATE_UNIVERSE_NOT_FULLY_ASSESSED"\n        )\n\n    context_count = disposition[\n        "context_selected_count"\n    ]\n\n    universe_count = disposition[\n        "candidate_universe_count"\n    ]\n\n    context_cap = int(\n        trace.get(\n            "model_context_cap",\n            trace.get(\n                "support_context_cap",\n                trace.get(\n                    "attack_context_cap",\n                    trace.get(\n                        "top_k",\n                        0,\n                    ),\n                ),\n            ),\n        )\n        or 0\n    )\n\n    context_packing_status = (\n        "ALL_UNIVERSE_IN_CONTEXT"\n        if (\n            universe_count\n            <= context_count\n        )\n        else "PARTIAL_CONTEXT_BATCH"\n    )\n\n    identity_gap_ids = (\n        disposition[\n            "universe_conditional_candidate_ids"\n        ]\n    )\n\n    identity_gap_decisiveness = (\n        "UNRESOLVED"\n        if identity_gap_ids\n        else "NONE"\n    )\n\n    parse_gap_ids = list(\n        trace.get(\n            "parse_gap_ids",\n            [],\n        )\n        or []\n    )\n\n    missing_required_track_types = list(\n        trace.get(\n            "missing_required_track_types",\n            [],\n        )\n        or []\n    )\n\n    if parse_gap_ids:\n        status = (\n            "INCOMPLETE_PARSE"\n        )\n\n    elif missing_required_track_types:\n        status = (\n            "INCOMPLETE_MISSING_SOURCE"\n        )\n\n    elif failed_channels:\n        status = (\n            "INCOMPLETE_CHANNEL_FAILURE"\n        )\n\n    elif (\n        not candidate_universe_preserved\n        or unassessed\n    ):\n        status = (\n            "LIMITED_TOP_K"\n        )\n\n    else:\n        has_relevant_alignment = any(\n            (\n                row.get(\n                    "relation"\n                )\n                in {\n                    "SUPPORT",\n                    "ATTACK",\n                }\n            )\n            and need_id\n            in (\n                row.get(\n                    "retrieval_need_ids",\n                    [],\n                )\n                or []\n            )\n            for row\n            in alignments\n        )\n\n        status = (\n            "COMPLETE"\n            if has_relevant_alignment\n            else "COMPLETE_NO_RELEVANT_HIT"\n        )\n\n    coverage_pass = status in {\n        "COMPLETE",\n        "COMPLETE_NO_RELEVANT_HIT",\n    }\n\n    report = {\n        "coverage_id":\n            stable_id(\n                requirement_id,\n                need_id,\n                direction,\n            ),\n        "need_id":\n            need_id,\n        "requirement_id":\n            requirement_id,\n        "direction":\n            direction,\n\n        "required_level":\n            "FULL_CASE_SCAN",\n        "conclusion_scope":\n            "SOURCE_PACKAGE_ONLY",\n\n        "indexed_record_count":\n            int(\n                trace.get(\n                    "retrieval_scan_chunk_count",\n                    0,\n                )\n                or 0\n            ),\n        "searchable_record_count":\n            int(\n                trace.get(\n                    "retrieval_scan_chunk_count",\n                    0,\n                )\n                or 0\n            ),\n        "scanned_record_count":\n            max(\n                int(\n                    lexical.get(\n                        "scanned_record_count",\n                        0,\n                    )\n                    or 0\n                ),\n                int(\n                    typed.get(\n                        "scanned_record_count",\n                        0,\n                    )\n                    or 0\n                ),\n                int(\n                    structure.get(\n                        "scan_chunk_count",\n                        0,\n                    )\n                    or 0\n                ),\n            ),\n\n        "query_plan_mode":\n            trace.get(\n                "query_plan_mode"\n            ),\n        "query_variant_ids": [\n            str(\n                variant.get(\n                    "variant_id",\n                    "",\n                )\n            )\n            for variant\n            in trace.get(\n                "query_variants",\n                [],\n            )\n            if variant.get(\n                "variant_id"\n            )\n        ],\n\n        "required_channels":\n            list(\n                REQUIRED_CHANNELS\n            ),\n        "executed_channels":\n            executed_channels,\n        "failed_channels":\n            failed_channels,\n        "channel_reports":\n            channels,\n\n        "candidate_count":\n            universe_count,\n        "candidate_universe_preserved":\n            candidate_universe_preserved,\n        "model_context_count":\n            context_count,\n        "model_context_cap":\n            context_cap,\n        "context_packing_status":\n            context_packing_status,\n\n        "deterministically_assessed_count":\n            len(\n                disposition[\n                    "universe_excluded_candidate_ids"\n                ]\n            ),\n        "model_or_rule_assessed_count":\n            len(\n                disposition[\n                    "model_or_rule_assessed_parent_ids"\n                ]\n            ),\n        "unassessed_candidate_ids":\n            unassessed,\n\n        "direct_alignment_ids": [\n            alignment_id(\n                row\n            )\n            for row\n            in alignments\n            if (\n                need_id\n                in (\n                    row.get(\n                        "retrieval_need_ids",\n                        [],\n                    )\n                    or []\n                )\n                and row.get(\n                    "argument_admission_channel"\n                )\n                == "DIRECT"\n            )\n        ],\n        "conditional_alignment_ids": [\n            alignment_id(\n                row\n            )\n            for row\n            in alignments\n            if (\n                need_id\n                in (\n                    row.get(\n                        "retrieval_need_ids",\n                        [],\n                    )\n                    or []\n                )\n                and row.get(\n                    "argument_admission_channel"\n                )\n                == "CONDITIONAL"\n            )\n        ],\n        "excluded_hit_ids":\n            disposition[\n                "universe_excluded_candidate_ids"\n            ],\n\n        "parse_gap_ids":\n            parse_gap_ids,\n        "identity_gap_ids":\n            identity_gap_ids,\n        "identity_gap_decisiveness":\n            identity_gap_decisiveness,\n        "missing_required_track_types":\n            missing_required_track_types,\n\n        "status":\n            status,\n        "coverage_pass":\n            coverage_pass,\n        "limiting_factors":\n            sorted(\n                set(\n                    limiting_factors\n                )\n            ),\n\n        "legacy_trace_coverage_status":\n            trace.get(\n                "coverage_status"\n            ),\n\n        "policy_notes": [\n            "top-k/context cap never implies COMPLETE",\n            "candidate universe and model context are separate",\n            "universe candidates need deterministic or model disposition",\n            "conditional identity is preserved as a gap signal",\n            "no adverse inference is produced by this artifact",\n        ],\n    }\n\n    report[\n        "coverage_sha256"\n    ] = sha256_json(\n        report\n    )\n\n    return report\n'


def replace_top_level_function(
    src: str,
    name: str,
    replacement: str,
) -> str:
    tree = ast.parse(src)

    matches = [
        node
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and node.name == name
    ]

    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one top-level function {name}, "
            f"found {len(matches)}"
        )

    node = matches[0]

    lines = src.splitlines(
        keepends=True
    )

    lines[
        node.lineno - 1:
        node.end_lineno
    ] = [
        replacement.rstrip()
        + "\n\n"
    ]

    return "".join(
        lines
    )


for path in (
    EVIDENCE,
    IDENTITY,
    COVERAGE,
):
    if not path.exists():
        raise SystemExit(
            f"Missing {path}; run from ~/freca/core_v1"
        )

evidence_src = EVIDENCE.read_text(
    encoding="utf-8"
)

identity_src = IDENTITY.read_text(
    encoding="utf-8"
)

coverage_src = COVERAGE.read_text(
    encoding="utf-8"
)

# Structural preflight against the current Core generation.
for marker in (
    "SUPPORT_VARIANT_UNION_TYPED_PLUS_STRUCTURE",
    "ATTACK_VARIANT_UNION_PLUS_STRUCTURE",
    "typed_fact_scan",
    "STRUCTURE_NEIGHBOUR",
    "run_requirement_reasoning",
):
    if marker not in evidence_src:
        raise SystemExit(
            "Unexpected evidence_reasoning_v2.py; "
            f"missing marker: {marker}"
        )

for marker in (
    "apply_identity_gate_to_traces",
    "identity_use_decision",
    "ADMIT_CONDITIONAL",
):
    if marker not in identity_src:
        raise SystemExit(
            "Unexpected identity_admissibility_v1.py; "
            f"missing marker: {marker}"
        )

for marker in (
    "candidate_disposition",
    "structure_channel",
    "evaluate_need_coverage",
    "top_k_never_means_complete",
):
    if marker not in coverage_src:
        raise SystemExit(
            "Unexpected coverage_v1.py; "
            f"missing marker: {marker}"
        )

patched_evidence = replace_top_level_function(
    evidence_src,
    "retrieve_requirement_candidates",
    NEW_RETRIEVE,
)

patched_identity = replace_top_level_function(
    identity_src,
    "apply_identity_gate_to_traces",
    NEW_IDENTITY,
)

patched_coverage = replace_top_level_function(
    coverage_src,
    "candidate_disposition",
    NEW_CANDIDATE_DISPOSITION,
)

patched_coverage = replace_top_level_function(
    patched_coverage,
    "structure_channel",
    NEW_STRUCTURE_CHANNEL,
)

patched_coverage = replace_top_level_function(
    patched_coverage,
    "evaluate_need_coverage",
    NEW_EVALUATE_NEED,
)

# Hard parse before any write.
ast.parse(
    patched_evidence
)
ast.parse(
    patched_identity
)
ast.parse(
    patched_coverage
)

for marker in (
    '"candidate_universe"',
    '"candidate_universe_persisted"',
    '"model_context_candidate_ids"',
    '"LEXICAL_BM25_FULL_HIT"',
    '"TYPED_FACT_SCAN"',
):
    if marker not in patched_evidence:
        raise RuntimeError(
            "Patched evidence module missing: "
            + marker
        )

for marker in (
    '"summary_scope"',
    '"CANDIDATE_UNIVERSE"',
):
    if marker not in patched_identity:
        raise RuntimeError(
            "Patched identity module missing: "
            + marker
        )

for marker in (
    "CANDIDATE_UNIVERSE_NOT_FULLY_ASSESSED",
    "context_packing_status",
    "candidate_universe_count",
):
    if marker not in patched_coverage:
        raise RuntimeError(
            "Patched coverage module missing: "
            + marker
        )

backups = {
    EVIDENCE:
        Path(
            "evidence_reasoning_v2.before_candidate_universe_v1.py"
        ),
    IDENTITY:
        Path(
            "identity_admissibility_v1.before_candidate_universe_v1.py"
        ),
    COVERAGE:
        Path(
            "coverage_v1.before_candidate_universe_v1.py"
        ),
}

for source_path, backup_path in backups.items():
    if not backup_path.exists():
        shutil.copy2(
            source_path,
            backup_path,
        )

tmp_e = Path(
    "evidence_reasoning_v2.candidate_universe_v1.tmp"
)
tmp_i = Path(
    "identity_admissibility_v1.candidate_universe_v1.tmp"
)
tmp_c = Path(
    "coverage_v1.candidate_universe_v1.tmp"
)

tmp_e.write_text(
    patched_evidence,
    encoding="utf-8",
)
tmp_i.write_text(
    patched_identity,
    encoding="utf-8",
)
tmp_c.write_text(
    patched_coverage,
    encoding="utf-8",
)

# Parse the exact install bytes.
ast.parse(
    tmp_e.read_text(
        encoding="utf-8"
    )
)
ast.parse(
    tmp_i.read_text(
        encoding="utf-8"
    )
)
ast.parse(
    tmp_c.read_text(
        encoding="utf-8"
    )
)

tmp_e.replace(
    EVIDENCE
)
tmp_i.replace(
    IDENTITY
)
tmp_c.replace(
    COVERAGE
)

print(
    "Installed FRECA candidate-universe separation v1."
)
print(
    "  lexical: full positive-hit universe persisted"
)
print(
    "  typed: full-case scan for SUPPORT and ATTACK"
)
print(
    "  structure: neighbour rescue preserved, still NOT full coverage"
)
print(
    "  model context: bounded subset remains in trace['candidates']"
)
print(
    "  coverage: now audits full universe disposition"
)
print()
print("Backups:")
for backup in backups.values():
    print(" ", backup)
