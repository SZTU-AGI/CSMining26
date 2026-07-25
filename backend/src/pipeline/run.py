"""端到端 FRECA 合规审计编排（全量 100 case × 41 CP = 4100 判决）。

设计约束（见 CODE_STANDARD.md §9/§11）：
- 红线：推理绝不读 checkingpoints 映射(row2/3)；仅用 CP 定义(row1) 抽成的 cp_definitions.yaml
  作法规检索种子。run.py 不直接 import/读红线 xlsx。
- 可复现：EXACT_PROMPT + deepseek-v4-flash + temperature=0 + thinking=disabled。
- 检索：BM25 + Qwen3-Embedding(RRF) → Qwen3-Reranker → MMR。Qwen3 权重全局只加载一次，
  各 case 复用同一份模型实例（dense_index/reranker 已支持 model/tokenizer 注入），
  各 case 独立 build_corpus 不互相覆盖。
- 并发：本地检索(主线程逐 case 顺序, 避免 GPU 竞争) + LLM API(ThreadPoolExecutor, 网络 I/O 密集)。
- 续跑：submission_inprogress.jsonl + progress.json 增量落盘；中断后 --resume 跳过已完成。
- 审计日志(audit.jsonl)：每条判决结构化一行(JSONL)，含 case/cp/verdict/tokens/attempts/latency/retry_reason；
  红线映射表(checkingpoints)绝不进日志(仅记 clause_id)。
- 全链路可追踪(retrieval_prompts.jsonl)：记录每个 CP 实际喂给 LLM 的 ev_query/policy_texts/evidence_texts，
  均来自法规 PDF + 农场证据 + CP 定义(非红线映射表)，满足复盘/可复现需求(见 2026-07-18 决策)。
- 可复现清单(prompt_manifest.json)：记录 EXACT_PROMPT / 模型 / temperature / thinking / 指令，提交用。
- 计费：全局累加 prompt/completion tokens，按 config.llm.pricing 结算(USD + 折算 CNY)。
- 终态：写 submission_<run_id>.xlsx，校验填满 4100 格后备份。
- Agent 自检层(src/llm/self_check.py)：①检索充分性评估→补充检索(规则,不烧LLM) ②自我质疑纠错(≤2轮)
  ③单模型双视角验证器 ④Element 一致性检查(case级,规则)。自检输入仅 policy+evidence 原文；
  视角指令(CRITIQUE/VERIFY_*)由代码生成附加到 EXACT_PROMPT，主判决 EXACT_PROMPT 不变；绝不引用 checkingpoints。
"""
import os
import sys
import json
import time
import threading
import signal
import argparse
import logging
import datetime as dt

# 加载 .env(若存在)。幂等；云端 run_retrieval.sh 已 export 的变量不会被覆盖。
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.utils.io import load_config
from src.parsing.case_parser import parse_case
from src.parsing.chunk_cache import parse_case_cached
from src.parsing.pdf_parser import parse_rules
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.query_builder import (
    RETRIEVE_INSTRUCTION, RERANK_INSTRUCTION, build_query_from_clause)
from src.retrieval.regulation_grounder import RegulationGrounder
from src.pipeline.run_state import RunState
from src.index.dense_index import DenseRetriever
from src.index.dense_api import DashScopeEmbedder
from src.retrieval.reranker import Qwen3Reranker
from src.retrieval.reranker_api import DashScopeReranker
from src.llm.auditor import DeepSeekAuditor
from src.llm.self_check import AgentSelfChecker, _merge_usage
from src.llm.prompt import (EXACT_PROMPT, CRITIQUE_PERSPECTIVE,
                            VERIFY_STRICT_PERSPECTIVE, VERIFY_LENIENT_PERSPECTIVE)

LOG = logging.getLogger("freca")

# 红线自检：仅针对「红线表(checkingpoints_all_elements_onesheet.xlsx)」的唯一指纹。
# 不用通用词组(如 "checking point")，避免误杀允许源(法规 PDF 本身可能含该通用词)。
# 结构保证：run.py 只加载 cp_definitions.yaml(已与红线 xlsx 物理隔离)，绝不打开红线 xlsx。
RED_LINE_FORBIDDEN = ("checkingpoints_all_elements_onesheet", "onesheet", "all elements onesheet")


def _red_line_clean(text: str) -> bool:
    t = (text or "").lower()
    return all(s not in t for s in RED_LINE_FORBIDDEN)


class FrecaPipeline:
    def __init__(self, cfg: dict, args: argparse.Namespace):
        self.cfg = cfg
        self.args = args
        self.stop = threading.Event()
        p = cfg["paths"]
        # 测试模式隔离: --cases / --cps / --dry-run-retrieval 的产物单独放 test_runs_dir, run_id 加 test_ 前缀
        self.is_test = bool(args.cases or args.cps or args.case_id or args.dry_run_retrieval)
        if self.is_test:
            self.runs_dir = p.get("test_runs_dir", p["runs_dir"])
            self.run_id = "test_" + dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        else:
            self.runs_dir = p["runs_dir"]
            self.run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

        # 1) CP 定义(红线允许, 来自 yaml 非 xlsx)
        cp_defs = self._load_cp_definitions(p["cp_definitions"])
        self.cp_defs = cp_defs
        self.cp_list = [f"CP{i+1}" for i in range(len(cp_defs))]

        # 2) 解析法规(缓存)
        use_docling = cfg["retrieval"]["parser"] == "docling"
        self.clauses = parse_rules(p["rules_pdf"], use_docling=use_docling,
                                   cache_md=p.get("rules_md"))
        self.reg_grounder = RegulationGrounder(self.clauses,
                                               top_k=cfg["retrieval"].get("regulation_top_k", 3))

        # 3) 红线自检
        self._red_line_self_check()

        # 4) 加载向量检索后端(全局一次, 后续各 case 复用实例)
        #    vector_backend=dashscope -> 阿里百炼 API 全局单例(无 GPU); local -> 本地 Qwen3-4B @ GPU
        self._load_vector_backend(use_docling)

        # 5) DeepSeek 判决客户端
        self.auditor = DeepSeekAuditor(
            model=cfg["llm"]["model"], temperature=cfg["llm"]["temperature"],
            api_key=os.environ.get(cfg["llm"].get("api_key_env", "DEEPSEEK_API_KEY")),
            api_base=cfg["llm"].get("api_base", "https://api.deepseek.com"),
            max_retries=cfg["llm"].get("max_retries", 3),
            thinking_mode=cfg["llm"].get("thinking_mode", "disabled"),
            timeout=cfg["llm"].get("timeout", 60))
        # 5.5) Agent 自检层（检索充分性评估 / 自我质疑纠错 / 双视角验证器 / Element 一致性）
        self.self_checker = AgentSelfChecker(self.auditor, cfg, self.cp_defs)

        # 6) 运行态
        self.state = RunState(self.runs_dir, self.run_id)
        self._add_file_log(self.state.run_dir)  # 可追溯运行日志(§9.4)
        # 7) 可复现清单(提交用): 记录 exact prompt + 模型 + 指令（红线映射表绝不进 prompt）
        self.state.record_manifest({
            "run_id": self.run_id,
            "system_prompt": EXACT_PROMPT,
            "model": cfg["llm"]["model"],
            "temperature": cfg["llm"]["temperature"],
            "thinking": cfg["llm"].get("thinking_mode", "disabled"),
            "retrieve_instruction": RETRIEVE_INSTRUCTION,
            "rerank_instruction": RERANK_INSTRUCTION,
            "per_cp_inputs": "retrieval_prompts.jsonl (ev_query / policy_texts / evidence_texts)",
            "note": "红线映射表(checkingpoints)未进入任何 prompt/语料; 推理仅来自法规 PDF+农场证据+CP定义; "
                    "Agent 自检视角(CRITIQUE/VERIFY_*)由代码生成附加到 EXACT_PROMPT, 主判决 EXACT_PROMPT 不变",
        })
        LOG.info(f"run_id={self.run_id} | {len(self.cp_defs)} CPs | "
                 f"{len(self.clauses)} regulation clauses | already_completed={self.state.n_completed}")

    # ---------- 初始化辅助 ----------
    def _add_file_log(self, run_dir: str):
        fh = logging.FileHandler(os.path.join(run_dir, "run.log"), encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(fh)

    def _load_cp_definitions(self, path: str) -> dict:
        import yaml
        if not os.path.isfile(path):
            raise FileNotFoundError(f"cp_definitions 缺失: {path}（红线允许资产, 由 scripts/extract_cp_definitions.py 生成）")
        d = yaml.safe_load(open(path, encoding="utf-8"))
        defs = d.get("cp_definitions", {})
        if not defs:
            raise ValueError("cp_definitions.yaml 为空")
        return defs

    def _red_line_self_check(self):
        assert self.cfg.get("constraint", {}).get("forbid_checkingpoints_in_ai", False), \
            "constraint.forbid_checkingpoints_in_ai 必须为 true（红线总开关）"
        # 结构保证：CP 定义来源(允许)必须 ≠ 红线 xlsx(禁用)
        red_xlsx = self.cfg["paths"].get("checkingpoints", "")
        cp_path = self.cfg["paths"].get("cp_definitions", "")
        assert cp_path and red_xlsx and os.path.abspath(cp_path) != os.path.abspath(red_xlsx), \
            "cp_definitions 路径必须与红线 checkingpoints xlsx 不同(物理隔离)"
        blob_sources = {
            "EXACT_PROMPT": EXACT_PROMPT,
            "RETRIEVE_INSTRUCTION": RETRIEVE_INSTRUCTION,
            "RERANK_INSTRUCTION": RERANK_INSTRUCTION,
            "CRITIQUE_PERSPECTIVE": CRITIQUE_PERSPECTIVE,
            "VERIFY_STRICT_PERSPECTIVE": VERIFY_STRICT_PERSPECTIVE,
            "VERIFY_LENIENT_PERSPECTIVE": VERIFY_LENIENT_PERSPECTIVE,
            "cp_definitions": json.dumps(self.cp_defs, ensure_ascii=False),
            "regulation_clauses": "\n".join(c.get("text", "") for c in self.clauses),
        }
        for name, text in blob_sources.items():
            assert _red_line_clean(text), f"红线自检失败: {name} 含 forbidden 子串(引用了 checkingpoints)"
        LOG.info("[red-line] self-check passed (无 checkingpoints 引用; 仅用 CP 定义作种子)")

    def _load_vector_backend(self, use_docling: bool):
        """加载向量检索后端（全局一次，后续各 case 复用）。

        vector_backend=dashscope -> 阿里百炼 API 全局单例（无 GPU，无需错峰）。
        vector_backend=local    -> 本地 Qwen3-4B @ GPU（延迟加载 reranker + GPU 错峰，防 24GB OOM）。
        """
        rc = self.cfg["retrieval"]
        self.vector_backend = rc.get("vector_backend", "local")
        self.base_dense = None
        self.base_rerank = None

        if self.vector_backend == "dashscope":
            va = self.cfg.get("vector_api", {})
            api_key = os.environ.get(va.get("api_key_env", "DASHSCOPE_API_KEY"))
            api_base = va.get("api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            if rc.get("use_dense", True):
                self.base_dense = DashScopeEmbedder(
                    api_key=api_key, api_base=api_base,
                    model=va.get("embed_model", "text-embedding-v3"),
                    batch_size=va.get("embed_batch_size", 32),
                    instruction=RETRIEVE_INSTRUCTION,
                    timeout=va.get("timeout", 60),
                    max_retries=va.get("max_retries", 3),
                    rate_limit_delay=va.get("rate_limit_delay", 0.2))
            if rc.get("use_reranker", True):
                # API 模式无 GPU，直接创建全局单例（无延迟加载/错峰）
                self.base_rerank = DashScopeReranker(
                    api_key=api_key, api_base=api_base,
                    model=va.get("rerank_model", "qwen3-rerank"),
                    top_n_default=rc.get("rerank_top_n", 20),
                    instruction=RERANK_INSTRUCTION,
                    timeout=va.get("timeout", 60),
                    max_retries=va.get("max_retries", 3),
                    rate_limit_delay=va.get("rate_limit_delay", 0.2))
            LOG.info(f"[vector] backend=dashscope dense={getattr(self.base_dense,'kind',None)} "
                     f"rerank={getattr(self.base_rerank,'kind',None)}")
            return

        # ---- local backend: 本地 Qwen3-4B @ GPU ----
        # 云端: 从 config 把 Qwen3 本地权重路径 setdefault 到 env, 避免回退 HF Hub 在线拉取(401/无网)。
        # 仅当 env 未显式设置(如 run_retrieval.sh 已 export)时才用 config 值; 本地无权重则不动(走 HF id/fallback)。
        q = self.cfg.get("qwen3", {})
        for envk, cfgk in (("QWEN3_EMBEDDING_PATH", "embedding_path"),
                           ("QWEN3_RERANKER_PATH", "reranker_path")):
            v = (q.get(cfgk) or "").strip()
            if v:
                os.environ.setdefault(envk, v)
        if rc.get("use_dense", True):
            self.base_dense = DenseRetriever(
                model_name=rc.get("dense_model", "Qwen/Qwen3-Embedding-4B"),
                instruction=RETRIEVE_INSTRUCTION,
                use_fp16=rc.get("use_fp16", True))
        if rc.get("use_reranker", True):
            # reranker 延迟加载: 在第一个 case 证据 corpus 编码(GPU dense)完成后,
            # dense 移 CPU 再把 reranker 上 GPU, 错峰避免两个 4B 同时占满 24GB 显存 OOM。
            self.base_rerank = None
        LOG.info(f"[qwen3] dense={getattr(self.base_dense,'kind',None)} "
                 f"reranker=deferred(loaded after first corpus encode)")

    def _ensure_reranker_on_gpu(self):
        """延迟创建 reranker(构造在 CPU, 0 GPU 占用); 真正上 GPU 由 HybridRetriever.retrieve
        内部错峰管理(与 dense 交替, 保证 GPU 同时仅 1 个 4B, 避免 24GB OOM)。

        API backend 下 reranker 已在 _load_vector_backend 创建为全局单例, 直接返回。
        """
        if self.vector_backend != "local":
            return
        rc = self.cfg["retrieval"]
        if not rc.get("use_reranker", True):
            return
        if self.base_rerank is None:
            self.base_rerank = Qwen3Reranker(
                model_name=rc.get("reranker_model", "Qwen/Qwen3-Reranker-4B"),
                instruction=RERANK_INSTRUCTION, device="cpu")
            LOG.info(f"[qwen3] reranker loaded (kind={getattr(self.base_rerank,'kind',None)}, device=cpu-deferred)")

    def _build_case_retriever(self, case_id: str):
        """解析一个 case 并构建 HybridRetriever(复用全局 Qwen3 权重, 独立 corpus 编码)。

        显存错峰：证据 corpus 编码时 dense 在 GPU；编码完 dense 移 CPU、reranker 上 GPU，
        保证 GPU 同一时刻只有 1 个 4B 模型, 避免 24GB 显存被两个 4B fp16 占满而 OOM。
        query 编码(单句)走 CPU dense, 重排走 GPU reranker。
        """
        case_dir = os.path.join(self.cfg["paths"]["cases_dir"], case_id)
        if not os.path.isdir(case_dir):
            return None
        rc = self.cfg["retrieval"]
        cache_dir = self.cfg["paths"].get("chunk_cache_dir")
        if cache_dir:
            chunks, _hit = parse_case_cached(
                case_dir, cache_dir=cache_dir,
                use_docling=(rc["parser"] == "docling"),
                chunk_size=rc["chunk_size"], chunk_overlap=rc["chunk_overlap"])
        else:
            chunks = parse_case(case_dir, use_docling=(rc["parser"] == "docling"),
                                chunk_size=rc["chunk_size"], chunk_overlap=rc["chunk_overlap"])
        if not chunks:
            return None
        use_dense = rc.get("use_dense", True)
        use_rerank = rc.get("use_reranker", True)
        if self.vector_backend == "local":
            # 错峰: dense 编码前先把 reranker 移回 CPU, 保证 GPU 同时仅 1 个 4B, 避免 OOM
            if use_rerank and self.base_rerank is not None:
                self.base_rerank.offload_to_cpu()
            # 阶段1: dense 上 GPU, 编码证据 corpus (HybridRetriever 构造时 build_corpus)
            if use_dense and self.base_dense is not None:
                self.base_dense.to_gpu()
                dense = DenseRetriever(model=self.base_dense.model,
                                       tokenizer=self.base_dense.tokenizer,
                                       instruction=RETRIEVE_INSTRUCTION,
                                       use_fp16=rc.get("use_fp16", True))
            else:
                dense = None
        else:
            # API backend(dashscope): 无 GPU, 复用全局无状态单例, 跳过所有错峰逻辑
            dense = self.base_dense if use_dense else None
        retr = HybridRetriever(
            chunks, top_k=rc.get("top_k", 8), rrf_k=rc.get("rrf_k", 60),
            use_dense=use_dense, dense=dense, instruction=RETRIEVE_INSTRUCTION,
            use_reranker=False, reranker=None,   # 先不重排, build_corpus 用 GPU dense
            rerank_top_n=rc.get("rerank_top_n", 20),
            rerank_instruction=RERANK_INSTRUCTION,
            use_mmr=rc.get("use_mmr", True), mmr_lambda=rc.get("mmr_lambda", 0.5),
            final_k=rc.get("final_k", 8))
        # 阶段2: 设置 reranker(HybridRetriever.retrieve 内部使用)
        if use_rerank:
            if self.vector_backend == "local":
                self._ensure_reranker_on_gpu()  # 本地延迟上 GPU 错峰
            retr.use_reranker = True
            retr.reranker = self.base_rerank
        LOG.info(f"[retriever] built {case_id}: chunks={len(chunks)} "
                 f"dense={getattr(self.base_dense,'kind',None)} "
                 f"rerank={getattr(self.base_rerank,'kind',None)}")
        return retr, chunks

    # ---------- 辅助：合并两次检索结果(去重 by text, 保序) ----------
    def _merge_hits(self, a: list, b: list) -> list:
        seen, out = set(), []
        for h in list(a) + list(b):
            t = h.get("text", "")
            if t in seen:
                continue
            seen.add(t)
            out.append(h)
        return out

    # ---------- 单判决(仅 LLM; 检索已在主线程串行完成, 避免 GPU 多线程死锁) ----------
    def _audit_llm(self, case_id: str, cp: str, reg_hits: list, ev_hits: list) -> dict:
        cp_def = self.cp_defs.get(cp, {})
        element = cp_def.get("element", "")
        rec = {
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "run_id": self.run_id, "case_id": case_id, "cp": cp, "element": element,
            "verdict": None, "policy_clause_ids": [], "n_evidence": 0,
            "evidence_tracks": [], "usage": {}, "attempts": 0, "latency_s": 0.0,
            "error": None, "model": self.cfg["llm"]["model"],
            "temperature": self.cfg["llm"]["temperature"],
            "thinking": self.cfg["llm"].get("thinking_mode", "disabled"),
        }
        t0 = time.time()
        try:
            rec["policy_clause_ids"] = [h.get("clause_id") for h in reg_hits]
            policy_excerpts = [h.get("text", "") for h in reg_hits]
            rec["n_evidence"] = len(ev_hits)
            rec["evidence_tracks"] = sorted({h.get("track", "?") for h in ev_hits})
            evidence_excerpts = [h.get("text", "") for h in ev_hits]
            if not evidence_excerpts:
                # 无证据: 判 N/A(该 CP 不适用/无材料), 仍记审计
                rec["verdict"] = "N/A"
                rec["attempts"] = 0
                rec["latency_s"] = round(time.time() - t0, 3)
                return rec
            # LLM 首判(可能重试); 纯网络 I/O, 线程池并发安全
            verdict0, usage0 = self.auditor.audit(policy_excerpts, evidence_excerpts)
            rec["usage"] = usage0 or {}
            rec["attempts"] = (usage0 or {}).get("attempts", 0)
            if verdict0 is None:
                rec["verdict"] = None
                rec["error"] = "llm_returned_none(retries_exhausted)"
                rec["latency_s"] = round(time.time() - t0, 3)
                return rec
            # ---- Agent 自检层（仅追加复核，不改主判决 EXACT_PROMPT；红线安全：输入仅 policy+evidence）----
            sc = {"initial": verdict0}
            final_v = verdict0
            if self.self_checker.enable:
                # 2) 自我质疑纠错（≤max_rounds 轮，批判性视角重判直到稳定）
                final_v, rounds, critiques, crit_usage = self.self_checker.critique_loop(
                    policy_excerpts, evidence_excerpts, verdict0)
                sc["rounds"] = rounds
                sc["critiques"] = critiques
                rec["usage"] = _merge_usage(rec["usage"], crit_usage)
                # 3) 单模型双视角验证器（strict/lenient 重判，一致才采用）
                if self.self_checker.use_verifier:
                    vres, v_usage = self.self_checker.verify(policy_excerpts, evidence_excerpts, final_v)
                    sc["verifier"] = vres
                    rec["usage"] = _merge_usage(rec["usage"], v_usage)
                    final_v = self.self_checker.resolve(final_v, vres)
            rec["verdict"] = final_v
            rec["self_check"] = sc
            rec["latency_s"] = round(time.time() - t0, 3)
            return rec
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
            rec["latency_s"] = round(time.time() - t0, 3)
            return rec

    # ---------- 全链路可追踪(retrieval_prompts.jsonl + prompt_manifest.json) ----------
    def _build_prompt_record(self, case_id, cp, reg_query, ev_query, reg_hits, ev_hits) -> dict:
        """构造单个 CP 实际喂给 LLM 的输入快照（复盘/可复现用）。

        红线安全：所有文本来自法规 PDF(parse_rules) + 农场证据(parse_case) + CP 定义(yaml)，
        绝不来自 checkingpoints 映射表。文本截断到 prompt_text_cap 控体积。
        """
        cap = int(self.cfg.get("retrieval", {}).get("prompt_text_cap", 1500))
        cp_def = self.cp_defs.get(cp, {})

        def _cap(t):
            t = t or ""
            return t if len(t) <= cap else t[:cap] + "...[truncated]"

        return {
            "case_id": case_id, "cp": cp,
            "cp_title": cp_def.get("title", ""),
            "element": cp_def.get("element", ""),
            "reg_query": reg_query,
            "ev_query": ev_query,
            "policy_clause_ids": [h.get("clause_id") for h in reg_hits],
            "policy_texts": [_cap(h.get("text", "")) for h in reg_hits],
            "evidence_texts": [_cap(h.get("text", "")) for h in ev_hits],
        }

    # ---------- 运行 ----------
    def run(self):
        run_cfg = self.cfg.get("run", {})
        max_workers = self.args.max_workers or run_cfg.get("max_workers", 4)
        rate_delay = self.cfg["llm"].get("rate_limit_delay", 0.3)
        cases = self._list_cases()
        if self.args.cases:
            cases = cases[: self.args.cases]
        if self.args.case_id:
            cases = [self.args.case_id]
            cd = self.cfg["paths"].get("cases_dir", "")
            if cd and not os.path.isdir(os.path.join(cd, self.args.case_id)):
                LOG.warning(f"--case-id {self.args.case_id} 目录不存在: {cd}/{self.args.case_id}")
        cps = self.cp_list
        if self.args.cps:
            wanted = {f"CP{c}" if str(c).isdigit() else c for c in
                      [x.strip() for x in self.args.cps.split(",")]}
            cps = [c for c in self.cp_list if c in wanted]

        LOG.info(f"开始: {len(cases)} cases × {len(cps)} CPs | workers={max_workers} | "
                 f"resume={not self.args.no_resume} | dry_run_retrieval={self.args.dry_run_retrieval}")

        from concurrent.futures import ThreadPoolExecutor
        signal.signal(signal.SIGINT, lambda *a: self.stop.set())

        for case_id in cases:
            if self.stop.is_set():
                LOG.info("收到中断信号, 安全退出(已完成项已落盘)...")
                break
            # 跳过整 case 已完成
            if not self.args.no_resume and all(self.state.is_done(case_id, cp) for cp in cps):
                continue
            retr_chunks = self._build_case_retriever(case_id)
            if retr_chunks is None:
                LOG.warning(f"[{case_id}] 无解析产物, 跳过")
                continue
            retr, chunks = retr_chunks

            # ---- 主线程串行检索(单 CUDA 上下文, 避免多线程 GPU 死锁) ----
            plan = []  # (cp, reg_hits, ev_hits)
            for cp in cps:
                if (not self.args.no_resume) and self.state.is_done(case_id, cp):
                    continue
                if self.args.dry_run_retrieval:
                    # 仅检索, 不调 API; 记 work item(主线程单线程安全)
                    self._dry_retrieval(case_id, cp, retr, chunks)
                    continue
                cp_def = self.cp_defs.get(cp, {})
                title = cp_def.get("title", "")
                reg_query, reg_hits = self.reg_grounder.ground(title)
                ev_query = build_query_from_clause(reg_hits[0]) if reg_hits else title
                ev_hits = retr.retrieve(ev_query) if retr is not None else []
                # 检索充分性评估 -> 补充检索（规则触发，不烧 LLM）：证据空 / 已知 grounding 偏差章
                if self.self_checker.enable and self.self_checker.need_supplement(ev_hits, reg_hits, cp):
                    supp = retr.retrieve(title) if retr is not None else []  # 换 query（用 CP title）重搜
                    ev_hits = self._merge_hits(ev_hits, supp)
                    LOG.info(f"[retrieve][supplement] {cp}: ev={len(ev_hits)} after supplement")
                LOG.info(f"[retrieve] {cp} done: reg={len(reg_hits)} ev={len(ev_hits)}")
                plan.append((cp, reg_query, ev_query, reg_hits, ev_hits))

            if self.args.dry_run_retrieval:
                # dry-run 不调 LLM, 直接进下一 case
                del retr, chunks
                self._maybe_finalize()
                continue

            # ---- 线程池并发 LLM 判决(纯网络 I/O, 无 GPU, 安全) ----
            tasks = []
            case_recs = []
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                for cp, reg_query, ev_query, reg_hits, ev_hits in plan:
                    def job(cp=cp, reg_hits=reg_hits, ev_hits=ev_hits):
                        time.sleep(rate_delay)
                        return self._audit_llm(case_id, cp, reg_hits, ev_hits)
                    tasks.append((cp, reg_query, ev_query, reg_hits, ev_hits, ex.submit(job)))
                for cp, reg_query, ev_query, reg_hits, ev_hits, fut in tasks:
                    rec = fut.result()
                    case_recs.append(rec)
                    verdict_ok = rec["verdict"] is not None and rec["error"] is None
                    if verdict_ok:
                        u = rec.get("usage", {}) or {}
                        self.state.add_tokens(int(u.get("prompt_tokens", 0) or 0),
                                             int(u.get("completion_tokens", 0) or 0))
                    self.state.record(rec, verdict_ok)
                    # 全链路可追踪：记录本 CP 实际喂给 LLM 的 query / 政策 / 证据原文
                    self.state.record_prompt(
                        self._build_prompt_record(case_id, cp, reg_query, ev_query, reg_hits, ev_hits))
                    LOG.info(f"[{case_id}][{cp}] verdict={rec['verdict']} "
                             f"policy={rec['policy_clause_ids']} ev={rec['n_evidence']} "
                             f"tok={rec['usage'].get('prompt_tokens',0)+rec['usage'].get('completion_tokens',0)} "
                             f"err={rec['error']}")
            # ---- 4) Element 一致性检查（case 级，规则，不烧 LLM）----
            if self.self_checker.enable and self.self_checker.element_consistency and case_recs:
                report, _ = self.self_checker.check_element_consistency(case_id, case_recs)
                self.state.record_consistency(report)
                if report.get("conflicts"):
                    LOG.warning(f"[{case_id}] element consistency conflicts: {report['conflicts']}")
            # 释放本 case 的 corpus 编码(GPU 显存); reranker 移回 CPU, 留给下 case 的 dense 上 GPU(错峰)
            if self.vector_backend == "local" and self.cfg["retrieval"].get("use_reranker", True) and self.base_rerank is not None:
                self.base_rerank.offload_to_cpu()
            del retr, chunks
            self._maybe_finalize()

        self._print_cost_summary()
        self._maybe_finalize(force=True)
        LOG.info(f"结束 run_id={self.run_id} | 完成 {self.state.n_completed}/4100")

    def _dry_retrieval(self, case_id, cp, retr, chunks):
        """--dry-run-retrieval: 只做法规 grounding + 证据检索, 写出 work item(JSONL), 不调 LLM。
        同时写 retrieval_prompts.jsonl(全链路可追踪, 不花钱即可复盘检索质量)。"""
        cp_def = self.cp_defs.get(cp, {})
        title = cp_def.get("title", "")
        reg_query, reg_hits = self.reg_grounder.ground(title)
        ev_query = build_query_from_clause(reg_hits[0]) if reg_hits else title
        ev_hits = retr.retrieve(ev_query) if retr is not None else []
        item = {
            "case_id": case_id, "cp": cp, "element": cp_def.get("element", ""),
            "policy_clause_ids": [h.get("clause_id") for h in reg_hits],
            "evidence_tracks": sorted({h.get("track", "?") for h in ev_hits}),
            "n_evidence": len(ev_hits),
        }
        with open(os.path.join(self.state.run_dir, "retrieval_dryrun.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        # 全链路可追踪(检索侧, 即便 dry-run 也记录)
        self.state.record_prompt(
            self._build_prompt_record(case_id, cp, reg_query, ev_query, reg_hits, ev_hits))

    def _list_cases(self) -> list:
        d = self.cfg["paths"]["cases_dir"]
        if not os.path.isdir(d):
            return []
        return sorted(os.path.basename(x) for x in
                      [os.path.join(d, n) for n in os.listdir(d)]
                      if os.path.isdir(x))

    # ---------- 计费 / 终态 ----------
    def _print_cost_summary(self):
        pr = self.cfg["llm"].get("pricing", {})
        t = self.state.tokens
        in_miss = t["prompt"] / 1e6 * pr.get("input_cache_miss_per_m", 0.14)
        out = t["completion"] / 1e6 * pr.get("output_per_m", 0.28)
        total_usd = in_miss + out
        cny = total_usd * pr.get("usd_to_cny", 7.2)
        LOG.info(f"[cost] calls={t['calls']} prompt_tok={t['prompt']} completion_tok={t['completion']} "
                 f"≈ ${total_usd:.4f} (¥{cny:.2f}, 按 cache_miss 保守估算; 实际 hit 更低)")

    def _maybe_finalize(self, force: bool = False):
        if not force and self.state.n_completed < self.cfg.get("run", {}).get("n_total", 4100):
            return
        self._finalize_xlsx()

    def _finalize_xlsx(self):
        """汇聚 submission_inprogress.jsonl → submission_<run_id>.xlsx, 校验填满 4100。"""
        try:
            import openpyxl
        except ImportError:
            LOG.warning("[finalize] openpyxl 未安装, 跳过 xlsx(中间 jsonl 已完整)")
            return
        grid = {}
        with open(self.state.inprogress_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                grid.setdefault(r["case_id"], {})[r["cp"]] = r["verdict"]
        cases = self._list_cases()
        out = os.path.join(self.state.run_dir,
                           f"submission_{self.run_id}.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "All Elements"
        header = ["RE Number"] + [f"CP{i+1}" for i in range(len(self.cp_list))]
        ws.append(header)
        filled = 0
        for cid in cases:
            row = [cid]
            for cp in self.cp_list:
                v = grid.get(cid, {}).get(cp, "")
                row.append(v)
                if v not in (None, "", "None"):
                    filled += 1
            ws.append(row)
        wb.save(out)
        LOG.info(f"[finalize] wrote {out} | filled={filled}/{self.cfg.get('run',{}).get('n_total',4100)}")
        if filled != self.cfg.get("run", {}).get("n_total", 4100):
            LOG.warning(f"[finalize] 未填满 4100 (filled={filled}); 用 --resume 补齐失败/缺失项")


def _setup_logging(run_id_placeholder: str, runs_dir: str):
    LOG.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    LOG.addHandler(ch)
    # 文件 handler 在 RunState 创建后补; 此处仅控制台


def main():
    ap = argparse.ArgumentParser(description="FRECA 全量合规审计编排")
    ap.add_argument("--cases", type=int, default=0, help="只跑前 N 个 case(冒烟)")
    ap.add_argument("--case-id", type=str, default="",
                    help="指定单个 case_id 运行(如 RE-QLD-2021-0112), 优先级高于 --cases")
    ap.add_argument("--cps", type=str, default="", help="只跑指定 CP, 如 '1,2,3'")
    ap.add_argument("--max-workers", type=int, default=0, help="LLM 并发线程(默认 config run.max_workers)")
    ap.add_argument("--no-resume", action="store_true", help="从头开始(忽略已完成进度)")
    ap.add_argument("--dry-run-retrieval", action="store_true",
                    help="只做法规 grounding+证据检索, 写 retrieval_dryrun.jsonl, 不调 LLM API")
    ap.add_argument("--estimate-only", action="store_true",
                    help="只打印成本估算并退出(需 config 内假设 token 数)")
    ap.add_argument("--config", type=str, default="",
                    help="指定配置文件(默认 config/config.yaml)；云端用 config.cloud.yaml 覆盖 Windows 路径")
    args = ap.parse_args()

    cfg = load_config(args.config) if args.config else load_config()
    _setup_logging("", cfg["paths"]["runs_dir"])

    if args.estimate_only:
        _estimate_only(cfg)
        return

    pipe = FrecaPipeline(cfg, args)
    pipe.run()


def _estimate_only(cfg: dict):
    pr = cfg["llm"].get("pricing", {})
    n_total = cfg.get("run", {}).get("n_total", 4100)
    # 假设: 每条 input ≈ 1800 tok(EXACT_PROMPT+3条款+8证据), output ≈ 1 tok
    avg_in, avg_out = 1800, 1
    in_usd = n_total * avg_in / 1e6 * pr.get("input_cache_miss_per_m", 0.14)
    out_usd = n_total * avg_out / 1e6 * pr.get("output_per_m", 0.28)
    total = in_usd + out_usd
    LOG.info(f"[estimate] {n_total} 判决 | 假设 input≈{avg_in} output≈{avg_out} tok/次")
    LOG.info(f"[estimate] ≈ ${total:.4f} (¥{total*pr.get('usd_to_cny',7.2):.2f}, cache_miss 保守)")
    LOG.info(f"[estimate] 若 system 前缀命中缓存(cache_hit $0.028/M)可降至 ≈ "
             f"${n_total*avg_in/1e6*pr.get('input_cache_hit_per_m',0.028)+out_usd:.4f}")


if __name__ == "__main__":
    main()
