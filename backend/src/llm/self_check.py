"""Agent 自检层（架构图规划：检索充分性评估 / 自我质疑纠错 ≤2轮 / 独立验证器 / Element 一致性检查）。

设计约束（见 CODE_STANDARD.md §2/§11）：
- 红线：所有自检输入仅来自 policy_excerpts + evidence_excerpts（与 audit 同来源，来自法规 PDF+农场证据+CP定义），
  绝不引入 checkingpoints 映射表。自检用的「视角/质疑」指令由代码生成（见 prompt.py 的 *_PERSPECTIVE 常量），
  不引用红线条款号。
- 可复现：所有 LLM 调用 temperature=0（沿用 auditor）；critique/verify 的 perspective 固定并记录到 prompt_manifest。
- 成本：仅 critique_loop（≤max_rounds 次）与 verify（2 视角）烧 LLM；need_supplement 与 check_element_consistency
  是规则/本地计算，不烧 LLM。全量模式每个 CP 额外触发 质疑+验证器，case 级一致性每 case 1 次（纯规则）。
- 不依赖 torch / requests / API：auditor 以 duck-typing 注入（需 .audit(policy, evidence, perspective=None)->(verdict, usage)），
  测试用 FakeAuditor，可独立运行。
"""
import logging

from .prompt import (EXACT_PROMPT, CRITIQUE_PERSPECTIVE,
                     VERIFY_STRICT_PERSPECTIVE, VERIFY_LENIENT_PERSPECTIVE)

LOG = logging.getLogger("freca.self_check")


def _merge_usage(*usages):
    """合并多个 auditor 返回的 usage dict（{prompt_tokens, completion_tokens}）。"""
    out = {"prompt_tokens": 0, "completion_tokens": 0}
    for u in usages:
        if not u:
            continue
        out["prompt_tokens"] += int(u.get("prompt_tokens", 0) or 0)
        out["completion_tokens"] += int(u.get("completion_tokens", 0) or 0)
    return out


class AgentSelfChecker:
    def __init__(self, auditor, cfg: dict, cp_defs: dict):
        self.auditor = auditor  # duck-typing：需 .audit(policy, evidence, perspective=None)->(verdict, usage)
        sc = (cfg.get("agent_self_check") or {})
        self.enable = sc.get("enable", True)
        self.trigger = sc.get("trigger", "all")  # all | boundary
        self.max_rounds = int(sc.get("max_rounds", 2))
        self.use_verifier = sc.get("use_verifier", True)
        self.element_consistency = sc.get("element_consistency", True)
        self.rerank_sufficiency_threshold = float(sc.get("rerank_sufficiency_threshold", 0.0))
        self.bias_cps = set(sc.get("known_grounding_bias_cps", []) or [])
        self.cp_defs = cp_defs or {}

    # ---------- 1) 检索充分性评估（规则触发补充检索，不调 LLM）----------
    def need_supplement(self, ev_hits: list, reg_hits: list, cp: str) -> bool:
        """规则判断是否需要补充检索（主线程检索阶段调用，不烧 LLM）。

        触发条件：①证据为空；②该 CP 属已知 grounding 偏差章（reg 命中定义章，召回偏题）。
        rerank 分数阈值（rerank_sufficiency_threshold>0 时）作为可选补充信号。
        """
        if not ev_hits:
            return True
        if cp in self.bias_cps:
            return True
        if self.rerank_sufficiency_threshold > 0:
            top = max((float(h.get("score", 0) or 0) for h in ev_hits), default=0)
            if top < self.rerank_sufficiency_threshold:
                return True
        return False

    # ---------- 2) 自我质疑纠错（≤max_rounds 轮）----------
    def critique_loop(self, policy, evidence, verdict0):
        """LLM 以批判性视角重判，直到稳定或达 max_rounds。

        返回 (final_verdict, rounds, critiques, usage)。
        critiques: list of {round, verdict}（每轮批判性重判结果）。
        """
        verdict = verdict0
        critiques = []
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        for r in range(self.max_rounds):
            v, usage = self.auditor.audit(policy, evidence, perspective=CRITIQUE_PERSPECTIVE)
            total_usage = _merge_usage(total_usage, usage)
            if v is None:
                break
            critiques.append({"round": r + 1, "verdict": v})
            if v == verdict:
                # 批判性重判与当前 verdict 一致 -> 稳定，结束
                break
            verdict = v
        return verdict, len(critiques), critiques, total_usage

    # ---------- 3) 独立验证器（单模型双视角）----------
    def verify(self, policy, evidence, verdict):
        """同一 DeepSeek 用 strict / lenient 两视角重判。

        返回 (res_dict, usage)。res_dict={strict, lenient, agree}。
        agree=True 表示两视角均与 verdict 一致（高置信）；否则标记需复核。
        """
        vs, u1 = self.auditor.audit(policy, evidence, perspective=VERIFY_STRICT_PERSPECTIVE)
        vl, u2 = self.auditor.audit(policy, evidence, perspective=VERIFY_LENIENT_PERSPECTIVE)
        usage = _merge_usage(u1, u2)
        res = {"strict": vs, "lenient": vl,
               "agree": (vs == verdict and vl == verdict)}
        return res, usage

    def resolve(self, final_v, verify_res):
        """验证器结果采用逻辑：两视角一致 -> 维持 final_v；
        不一致 -> 取两视角共识（strict==lenient≠final_v 时采用该值）；否则维持批判性结论 final_v（留痕人工复核）。
        """
        if verify_res.get("agree"):
            return final_v
        s, l = verify_res.get("strict"), verify_res.get("lenient")
        if s is not None and s == l and s != final_v:
            return s
        return final_v

    # ---------- 4) Element 一致性检查（case 级，主线程调用，规则不烧 LLM）----------
    def check_element_consistency(self, case_id, recs):
        """同 case 内按 Element 分组检查 verdict 逻辑一致性（规则，不调 LLM）。

        输入 recs: 本 case 各 CP 的判决记录（含 cp/element/verdict/policy_clause_ids）。
        返回 (report_dict, usage=None)。report 记录各 Element 的 CP 分布，并标记
        「compliant(1) 与 non-compliant(0) 并存」的待复核信号（不改 verdict，交人工）。
        """
        by_elem = {}
        for r in recs:
            by_elem.setdefault(r.get("element", "?"), []).append(r)
        conflicts = []
        for elem, rs in by_elem.items():
            vs = {str(r.get("verdict")) for r in rs}  # verdict 可能是 int(1/0) 或 str("N/A")
            if "1" in vs and "0" in vs:
                conflicts.append({
                    "element": elem,
                    "cps": [x.get("cp") for x in rs],
                    "note": "compliant(1) 与 non-compliant(0) 并存，建议人工复核逻辑一致性",
                })
        report = {
            "case_id": case_id,
            "n_cp": len(recs),
            "elements": {k: [x.get("cp") for x in v] for k, v in by_elem.items()},
            "conflicts": conflicts,
        }
        return report, None
