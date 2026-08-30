from pathlib import Path

TARGET = Path("evidence_reasoning_v2.py")

if not TARGET.exists():
    raise SystemExit("Run this installer from the Core directory containing evidence_reasoning_v2.py")

s = TARGET.read_text(encoding="utf-8")


def replace_function(text: str, func_name: str, next_func_name: str, replacement: str) -> str:
    start_marker = f"def {func_name}("
    end_marker = f"\ndef {next_func_name}("

    start = text.find(start_marker)
    if start < 0:
        raise SystemExit(f"Could not find {start_marker}")

    end = text.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"Could not find {end_marker}")

    return text[:start] + replacement.rstrip() + "\n\n" + text[end + 1:]


new_build = r'''def build_retrieval_needs(plan: dict) -> list[dict]:
    needs = []

    for requirement in plan["requirements"]:
        query_facets = []
        query_variants = []
        seen = set()

        for index, source in enumerate(requirement["query_sources"], start=1):
            facet = normalize_ws(source["quote"])
            key = facet.lower()

            if not facet or key in seen:
                continue

            seen.add(key)
            query_facets.append(facet)

            query_variants.append(
                {
                    "variant_id": f"{requirement['requirement_id']}.source.{index}",
                    "source": source.get("source"),
                    "candidate_id": source.get("candidate_id"),
                    "query": facet,
                    "transformation": "EXACT_SOURCE_FACET",
                }
            )

        proposition = normalize_ws(requirement["proposition_to_establish"])
        proposition_key = proposition.lower()

        if proposition and proposition_key not in seen:
            query_facets.append(proposition)
            query_variants.append(
                {
                    "variant_id": f"{requirement['requirement_id']}.proposition",
                    "source": "EVIDENCE_REQUIREMENT",
                    "candidate_id": None,
                    "query": proposition,
                    "transformation": "FROZEN_PROPOSITION",
                }
            )

        for direction in ("SUPPORT", "ATTACK"):
            needs.append(
                {
                    "need_id": f"{requirement['requirement_id']}.{direction.lower()}",
                    "requirement_id": requirement["requirement_id"],
                    "atom_id": requirement["atom_id"],
                    "direction": direction,
                    "priority_class": (
                        "COUNTEREVIDENCE"
                        if direction == "ATTACK"
                        else (
                            "DECISIVE"
                            if requirement["decisiveness"] == "DECISIVE"
                            else "NON_DECISIVE"
                        )
                    ),
                    "query_facets": query_facets,
                    "query_variants": query_variants,
                    "coverage_requirement": "CANDIDATE_DISCOVERY",
                }
            )

    return needs
'''

s = replace_function(
    s,
    "build_retrieval_needs",
    "_build_query",
    new_build,
)

new_retrieve = r'''def retrieve_requirement_candidates(
    evidence_chunks: list[dict],
    needs: list[dict],
    *,
    top_k: int = 12,
) -> list[dict]:
    docs = []
    by_id = {}

    for chunk in evidence_chunks:
        cid = chunk_id(chunk)
        text = chunk_text(chunk)

        item = dict(chunk)
        item["id"] = cid
        item["text"] = text

        docs.append(item)
        by_id[cid] = item

    candidate_limit = 40
    rrf_k = 60
    per_variant_quota = 3
    attack_context_cap = max(24, top_k)

    bm25_cache = {}

    def rank_query(query: str, limit: int) -> list[dict]:
        query = normalize_ws(query)
        cache_key = query + "\n" + str(limit)

        if cache_key not in bm25_cache:
            bm25_cache[cache_key] = core.bm25_rank(query, docs, limit)

        return bm25_cache[cache_key]

    def neighbour_ids(evidence_id: str) -> list[str]:
        out = []

        paragraph = re.match(r"^(.*):P(\d+)$", evidence_id)

        if paragraph:
            prefix = paragraph.group(1)
            number = int(paragraph.group(2))

            for adjacent in (number - 1, number + 1):
                if adjacent >= 1:
                    out.append(f"{prefix}:P{adjacent}")

            return out

        row = re.match(r"^(.*):R(\d+)$", evidence_id)

        if row:
            prefix = row.group(1)
            number = int(row.group(2))

            for adjacent in (number - 1, number + 1):
                if adjacent >= 1:
                    out.append(f"{prefix}:R{adjacent}")

        return out

    def rrf_order(rankings: list[list[str]]) -> tuple[list[str], dict[str, float]]:
        totals = {}
        best_rank = {}
        first_seen = {}
        counter = 0

        for ranking in rankings:
            for rank, evidence_id in enumerate(ranking, start=1):
                totals[evidence_id] = (
                    totals.get(evidence_id, 0.0)
                    + 1.0 / (rrf_k + rank)
                )

                best_rank[evidence_id] = min(
                    best_rank.get(evidence_id, 10**9),
                    rank,
                )

                if evidence_id not in first_seen:
                    first_seen[evidence_id] = counter
                    counter += 1

        ordered = sorted(
            totals,
            key=lambda evidence_id: (
                -totals[evidence_id],
                best_rank[evidence_id],
                first_seen[evidence_id],
                evidence_id,
            ),
        )

        return ordered, totals

    traces = []

    for need in needs:
        combined_query = _build_query(need["query_facets"])

        if need["direction"] == "SUPPORT":
            ranking = rank_query(combined_query, top_k)

            candidates = [
                {
                    "evidence_id": item["id"],
                    "score": item.get("score"),
                    "text": item["text"],
                    "retrieval_methods": ["LEXICAL_BM25_COMBINED"],
                }
                for item in ranking
            ]

            traces.append(
                {
                    **need,
                    "query": combined_query,
                    "query_plan_mode": "SUPPORT_COMBINED_BASELINE",
                    "top_k": top_k,
                    "candidate_limit": top_k,
                    "candidate_count_checked_by_model": len(candidates),
                    "retrieval_scan_chunk_count": len(docs),
                    "coverage_status": "TOP_K_SEMANTIC_ALIGNMENT_PILOT",
                    "candidates": candidates,
                }
            )
            continue

        variant_rows = []
        rankings = []

        for variant in need.get("query_variants", []):
            query = normalize_ws(variant.get("query", ""))

            if not query:
                continue

            ranked = rank_query(query, candidate_limit)
            ids = [item["id"] for item in ranked]

            rankings.append(ids)

            variant_rows.append(
                {
                    **variant,
                    "candidate_limit": candidate_limit,
                    "top_ids": ids[:per_variant_quota],
                }
            )

        if not rankings:
            ranked = rank_query(combined_query, candidate_limit)
            ids = [item["id"] for item in ranked]

            rankings = [ids]

            variant_rows = [
                {
                    "variant_id": f"{need['need_id']}.fallback",
                    "source": "COMBINED",
                    "candidate_id": None,
                    "query": combined_query,
                    "transformation": "COMBINED_FALLBACK",
                    "candidate_limit": candidate_limit,
                    "top_ids": ids[:per_variant_quota],
                }
            ]

        fused_ids, rrf_scores = rrf_order(rankings)

        ordered_ids = []
        methods_by_id = {}

        def add_candidate(evidence_id: str, method: str) -> None:
            if evidence_id not in by_id:
                return

            methods_by_id.setdefault(evidence_id, [])

            if method not in methods_by_id[evidence_id]:
                methods_by_id[evidence_id].append(method)

            if evidence_id not in ordered_ids:
                ordered_ids.append(evidence_id)

        for variant in variant_rows:
            for evidence_id in variant["top_ids"]:
                add_candidate(evidence_id, "VARIANT_TOP")

                for neighbour_id in neighbour_ids(evidence_id):
                    add_candidate(neighbour_id, "STRUCTURE_NEIGHBOUR")

        for evidence_id in fused_ids:
            if len(ordered_ids) >= attack_context_cap:
                break

            add_candidate(evidence_id, "RRF_FILL")

        ordered_ids = ordered_ids[:attack_context_cap]

        candidates = []

        for evidence_id in ordered_ids:
            item = by_id[evidence_id]

            candidates.append(
                {
                    "evidence_id": evidence_id,
                    "score": rrf_scores.get(evidence_id),
                    "text": item["text"],
                    "retrieval_methods": methods_by_id.get(evidence_id, []),
                }
            )

        traces.append(
            {
                **need,
                "query": combined_query,
                "query_plan_mode": "ATTACK_VARIANT_UNION_PLUS_STRUCTURE",
                "query_variants": variant_rows,
                "top_k": top_k,
                "candidate_limit": candidate_limit,
                "per_variant_quota": per_variant_quota,
                "attack_context_cap": attack_context_cap,
                "candidate_count_checked_by_model": len(candidates),
                "retrieval_scan_chunk_count": len(docs),
                "coverage_status": "VARIANT_UNION_STRUCTURE_PILOT",
                "candidates": candidates,
            }
        )

    return traces
'''

s = replace_function(
    s,
    "retrieve_requirement_candidates",
    "_alignment_pairs",
    new_retrieve,
)

TARGET.write_text(s, encoding="utf-8")
print("Installed direction-aware counterevidence retrieval patch.")
