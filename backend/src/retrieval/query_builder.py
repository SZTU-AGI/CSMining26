"""Query / Instruction 构造：仅来自法规条款/术语，严禁引用 checkingpoints 表。

Boss 已确认红线：checkingpoints（CP↔法规条款映射）不得进任何 AI 输入。
因此检索 query 一律从法规 PDF 拆解出的条款原文构造，CP 编号只作输出对齐。

Qwen3 原生 instruction-aware（无 ICL），3-shot 改为 task instruction：
- RETRIEVE_INSTRUCTION：给 Dense(Embedding) 的 query 前缀
- RERANK_INSTRUCTION：给 Qwen3-Reranker 的任务指令
均不引用 checkingpoints，且使用英语（Qwen3 训练 instruction 多为英文）。
"""

# 检索任务指令（不引用 checkingpoints；来自任务本身：法规条款↔农场证据匹配）
RETRIEVE_INSTRUCTION = (
    "Given an export-control regulation clause, retrieve the relevant passages "
    "from the establishment's compliance evidence that confirm or contradict the requirement."
)
RERANK_INSTRUCTION = (
    "Given an export-control regulation clause, judge whether the evidence passage is "
    "relevant to confirming or contradicting that requirement."
)


def build_query_from_clause(clause: dict, max_chars: int = 600) -> str:
    """用法规条款原文（标题+正文）作 query。"""
    q = f"{clause.get('title', '')}. {clause.get('text', '')}"
    return q[:max_chars]


def build_query_from_keyword(keyword: str, rules_clauses: list, max_chars: int = 600) -> str:
    """在法规条款中找含 keyword 的条款，返回其原文作 query。"""
    kw = keyword.lower()
    for c in rules_clauses:
        if kw in (c.get("title", "") + c.get("text", "")).lower():
            return build_query_from_clause(c, max_chars)
    return keyword  # fallback：裸术语
