"""Agent 自检层纯 Python 单测（不依赖 torch / requests / API / 网络）。

用 FakeAuditor(duck-typing) 模拟 DeepSeek 判决，验证：
- 自我质疑纠错(critique_loop) 的轮数上限与稳定逻辑
- 单模型双视角验证器(verify) 一致/不一致
- resolve 采用逻辑
- 检索充分性(need_supplement) 规则
- Element 一致性(check_element_consistency) 矛盾检测
- 红线：自检视角常量不引用 checkingpoints
- run_state.record_consistency 落盘
"""
import os
import sys
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.llm.self_check import AgentSelfChecker, _merge_usage
from src.llm.prompt import (EXACT_PROMPT, CRITIQUE_PERSPECTIVE,
                            VERIFY_STRICT_PERSPECTIVE, VERIFY_LENIENT_PERSPECTIVE)
from src.pipeline.run_state import RunState

RED_LINE = ("checkingpoints_all_elements_onesheet", "onesheet", "all elements onesheet")


def _clean(t):
    return all(s not in (t or "").lower() for s in RED_LINE)


class FakeAuditor:
    """按预定义序列返回 verdict，记录每次调用的 perspective（验证自检视角注入）。"""
    def __init__(self, seq):
        self.seq = list(seq)
        self.i = 0
        self.calls = []
    def audit(self, policy, evidence, perspective=None):
        v = self.seq[self.i]
        self.i += 1
        self.calls.append(perspective)
        return v, {"prompt_tokens": 100, "completion_tokens": 1}


def test_critique_loop_stable():
    cfg = {"agent_self_check": {"enable": True, "max_rounds": 2, "use_verifier": False}}
    a = FakeAuditor([1, 1])  # 两次 critique 均返回 1
    sc = AgentSelfChecker(a, cfg, {})
    final, rounds, crit, usage = sc.critique_loop(["p"], ["e"], 0)  # 首判 0
    assert final == 1, final
    assert rounds == 2, rounds
    assert len(crit) == 2
    print("OK test_critique_loop_stable")


def test_critique_loop_immediate_stable():
    cfg = {"agent_self_check": {"enable": True, "max_rounds": 2, "use_verifier": False}}
    a = FakeAuditor([0, 0])
    sc = AgentSelfChecker(a, cfg, {})
    final, rounds, crit, usage = sc.critique_loop(["p"], ["e"], 0)
    assert final == 0
    assert rounds == 1, rounds  # 第一轮即稳定
    print("OK test_critique_loop_immediate_stable")


def test_critique_loop_max_rounds():
    cfg = {"agent_self_check": {"enable": True, "max_rounds": 2, "use_verifier": False}}
    a = FakeAuditor([1, 0])  # 首判0->crit1=1->crit2=0, 达2轮停(未稳定)
    sc = AgentSelfChecker(a, cfg, {})
    final, rounds, crit, usage = sc.critique_loop(["p"], ["e"], 0)
    assert final == 0
    assert rounds == 2
    print("OK test_critique_loop_max_rounds")


def test_verify_agree():
    cfg = {"agent_self_check": {"enable": True, "use_verifier": True}}
    a = FakeAuditor([1, 1])  # strict=1, lenient=1
    sc = AgentSelfChecker(a, cfg, {})
    res, usage = sc.verify(["p"], ["e"], 1)
    assert res["agree"] is True
    assert res["strict"] == 1 and res["lenient"] == 1
    print("OK test_verify_agree")


def test_verify_disagree():
    cfg = {"agent_self_check": {"enable": True, "use_verifier": True}}
    a = FakeAuditor([0, 1])  # strict=0, lenient=1
    sc = AgentSelfChecker(a, cfg, {})
    res, usage = sc.verify(["p"], ["e"], 1)
    assert res["agree"] is False
    print("OK test_verify_disagree")


def test_resolve():
    cfg = {"agent_self_check": {"enable": True}}
    sc = AgentSelfChecker(None, cfg, {})
    assert sc.resolve(1, {"agree": True, "strict": 1, "lenient": 1}) == 1
    assert sc.resolve(1, {"agree": False, "strict": 0, "lenient": 0}) == 0
    assert sc.resolve(1, {"agree": False, "strict": 0, "lenient": 1}) == 1
    print("OK test_resolve")


def test_need_supplement():
    cfg = {"agent_self_check": {"enable": True, "known_grounding_bias_cps": ["CP1", "CP2"]}}
    sc = AgentSelfChecker(None, cfg, {})
    assert sc.need_supplement([], [], "CP3") is True
    assert sc.need_supplement([{"text": "x"}], [], "CP3") is False
    assert sc.need_supplement([{"text": "x"}], [], "CP1") is True  # bias cp
    cfg2 = {"agent_self_check": {"enable": True, "rerank_sufficiency_threshold": 0.5,
                                 "known_grounding_bias_cps": []}}
    sc2 = AgentSelfChecker(None, cfg2, {})
    assert sc2.need_supplement([{"text": "x", "score": 0.2}], [], "CP3") is True
    assert sc2.need_supplement([{"text": "x", "score": 0.9}], [], "CP3") is False
    print("OK test_need_supplement")


def test_element_consistency():
    cfg = {"agent_self_check": {"enable": True, "element_consistency": True}}
    sc = AgentSelfChecker(None, cfg, {})
    recs = [
        {"cp": "CP1", "element": "E1", "verdict": 1, "policy_clause_ids": ["1-1"]},
        {"cp": "CP2", "element": "E1", "verdict": 0, "policy_clause_ids": ["1-2"]},
        {"cp": "CP3", "element": "E2", "verdict": 1, "policy_clause_ids": ["2-1"]},
    ]
    report, usage = sc.check_element_consistency("CASE_A", recs)
    assert usage is None
    assert len(report["conflicts"]) == 1
    assert report["conflicts"][0]["element"] == "E1"
    recs2 = [{"cp": "CP1", "element": "E1", "verdict": 1},
             {"cp": "CP3", "element": "E2", "verdict": 1}]
    report2, _ = sc.check_element_consistency("CASE_B", recs2)
    assert report2["conflicts"] == []
    print("OK test_element_consistency")


def test_red_line_perspectives():
    for name, p in [("CRITIQUE", CRITIQUE_PERSPECTIVE), ("VERIFY_STRICT", VERIFY_STRICT_PERSPECTIVE),
                    ("VERIFY_LENIENT", VERIFY_LENIENT_PERSPECTIVE)]:
        assert _clean(p), f"{name} 含红线指纹"
        assert "checkingpoints" not in p.lower()
    print("OK test_red_line_perspectives")


def test_merge_usage():
    m = _merge_usage({"prompt_tokens": 10, "completion_tokens": 1},
                     {"prompt_tokens": 20, "completion_tokens": 2}, None)
    assert m == {"prompt_tokens": 30, "completion_tokens": 3}
    print("OK test_merge_usage")


def test_run_state_consistency():
    tmp = tempfile.mkdtemp()
    st = RunState(tmp, "test_sc")
    st.record_consistency({"case_id": "C1", "n_cp": 2, "conflicts": [{"element": "E1"}]})
    path = os.path.join(st.run_dir, "consistency.jsonl")
    assert os.path.isfile(path)
    lines = [json.loads(l) for l in open(path, encoding="utf-8")]
    assert lines[0]["case_id"] == "C1"
    print("OK test_run_state_consistency")


if __name__ == "__main__":
    test_critique_loop_stable()
    test_critique_loop_immediate_stable()
    test_critique_loop_max_rounds()
    test_verify_agree()
    test_verify_disagree()
    test_resolve()
    test_need_supplement()
    test_element_consistency()
    test_red_line_perspectives()
    test_merge_usage()
    test_run_state_consistency()
    print("\nALL SELF_CHECK TESTS PASSED ✅")
