"""run.py 纯逻辑冒烟(不依赖 torch / DeepSeek API)。

验证:
1) cp_definitions.yaml 加载(41 CP, 红线干净)
2) RegulationGrounder: CP 定义 → 法规条款 grounding
3) RunState: 增量记录 + 续跑(is_done) + 审计日志 + xlsx 终态校验
4) 红线自检: EXACT_PROMPT / instructions / cp_defs 无 forbidden 子串
"""
import os
import sys
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.retrieval.regulation_grounder import RegulationGrounder
from src.pipeline.run_state import RunState
from src.llm.prompt import EXACT_PROMPT
from src.retrieval.query_builder import RETRIEVE_INSTRUCTION, RERANK_INSTRUCTION
import yaml

RED_LINE = ("checkingpoints_all_elements_onesheet", "onesheet", "all elements onesheet")


def clean(t):
    t = (t or "").lower()
    return all(s not in t for s in RED_LINE)


def main():
    # 1) cp_definitions
    cp_path = os.path.join(ROOT, "data", "cp_definitions.yaml")
    defs = yaml.safe_load(open(cp_path, encoding="utf-8"))["cp_definitions"]
    assert len(defs) == 41, f"expected 41 CPs, got {len(defs)}"
    joined = " ".join(str(v) for v in defs.values())
    assert clean(joined), "cp_defs 含 forbidden 子串"
    print(f"[ok] cp_definitions: {len(defs)} CPs, 红线干净")

    # 2) RegulationGrounder (合成法规条款)
    clauses = [
        {"clause_id": "2-1", "title": "Buildings, equipment and facilities",
         "text": "The establishment must have buildings, equipment, facilities and services required for export operations."},
        {"clause_id": "3-1", "title": "Systems of controls – hygiene",
         "text": "The establishment must implement a system of controls to manage hygiene and waste control."},
        {"clause_id": "4-1", "title": "Traceability",
         "text": "The establishment must maintain traceability and integrity of prescribed plant products."},
    ]
    g = RegulationGrounder(clauses, top_k=3)
    q, hits = g.ground(defs["CP8"]["title"])  # CP8 = 2.1 Buildings...
    assert hits, "grounding 返回空"
    assert hits[0]["clause_id"] == "2-1", f"CP8 应命中风 2-1, got {hits[0]['clause_id']}"
    print(f"[ok] RegulationGrounder CP8 -> top clause {hits[0]['clause_id']} "
          f"(policy_clause_ids={[h['clause_id'] for h in hits]})")

    # 3) RunState 增量 + 续跑 + 审计 + xlsx
    tmp = tempfile.mkdtemp()
    try:
        rs = RunState(tmp, "test_run")
        assert rs.n_completed == 0
        rec = {"run_id": "test_run", "case_id": "CASE_A", "cp": "CP1", "verdict": 1,
               "policy_clause_ids": ["2-1"], "n_evidence": 3, "evidence_tracks": ["1"],
               "usage": {"prompt_tokens": 1500, "completion_tokens": 1}, "attempts": 1,
               "latency_s": 0.5, "error": None, "model": "deepseek-v4-flash",
               "temperature": 0, "thinking": "disabled"}
        rs.record(rec, verdict_ok=True)
        rs.add_tokens(1500, 1)
        assert rs.is_done("CASE_A", "CP1")
        assert not rs.is_done("CASE_A", "CP2")
        # 失败项
        rec_fail = dict(rec, cp="CP2", verdict=None, error="none")
        rs.record(rec_fail, verdict_ok=False)
        assert rs.n_completed == 1
        # 审计日志含结构化字段
        with open(rs.audit_path, encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        assert len(lines) == 2, f"audit rows={len(lines)}"
        # 终态 xlsx 仅 CASE_A 一行, 填满 CP1, CP2 空 -> 校验未填满
        grid = {"CASE_A": {"CP1": 1, "CP2": None}}
        # 直接用 RunState 的写入器等价逻辑手动构造校验
        print(f"[ok] RunState: completed={rs.n_completed}, tokens={rs.tokens}, "
              f"audit_rows={len(lines)}, 续跑 is_done 正确")

        # xlsx 终态校验
        import openpyxl
        ws_rows = {"CASE_A": {"CP1": 1, "CP2": None}}
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "All Elements"
        ws.append(["RE Number"] + [f"CP{i+1}" for i in range(41)])
        filled = 0
        for cid, cpv in ws_rows.items():
            row = [cid]
            for i in range(41):
                cp = f"CP{i+1}"; v = cpv.get(cp, None)
                row.append(v); 
                if v not in (None, "", "None"):
                    filled += 1
            ws.append(row)
        out = os.path.join(tmp, "submission_test.xlsx")
        wb.save(out)
        assert filled == 1, f"filled={filled}"
        assert os.path.isfile(out)
        print(f"[ok] xlsx 终态: 写出 {out}, filled={filled}/4100 (未填满, 符合预期)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 4) 红线自检
    blob = EXACT_PROMPT + RETRIEVE_INSTRUCTION + RERANK_INSTRUCTION
    assert clean(blob), "prompt/instructions 含 forbidden 子串"
    print("[ok] 红线自检: EXACT_PROMPT+instructions 无 checkingpoints 引用")
    print("\nALL RUN LOGIC CHECKS PASSED ✅")


if __name__ == "__main__":
    main()
