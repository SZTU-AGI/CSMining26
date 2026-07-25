"""MMR（Maximal Marginal Relevance）去重：平衡相关性与多样性。

输入：候选列表（带 _score）+ 归一化 emb 矩阵（余弦=点积）+ lambda∈[0,1]
  lambda=1 -> 纯相关性；lambda=0 -> 纯多样性
流程：贪心选 max( (1-lambda)*rel - lambda*max_sim_to_selected )
相似度用 dense embedding 余弦（在小候选集上算，省显存）。

典型用法：RRF 融合后取宽 top-N -> Qwen3-Reranker 精排 -> MMR 去重冗余证据 -> final_k。
"""
import numpy as np


def mmr_select(candidates: list, emb_matrix: np.ndarray,
               lambda_: float = 0.5, topk: int = None):
    """
    candidates: list[dict]（需含 _score）
    emb_matrix: np.ndarray (n, dim) 已归一化 -> 余弦 = 点积
    返回 list[(idx, score)] 去重后按 MMR 得分排序
    """
    n = len(candidates)
    if n == 0:
        return []
    if emb_matrix.shape[0] != n:
        raise ValueError(f"emb_matrix rows {emb_matrix.shape[0]} != candidates {n}")
    sims = emb_matrix @ emb_matrix.T  # 余弦矩阵（已归一化）
    selected = []
    remaining = set(range(n))
    while remaining and (topk is None or len(selected) < topk):
        best, best_score = None, -1e18
        for i in remaining:
            rel = float(candidates[i].get("_score", 0.0))
            max_sim = max((sims[i, j] for j in selected), default=0.0)
            mmr = (1.0 - lambda_) * rel - lambda_ * max_sim
            if mmr > best_score:
                best_score, best = mmr, i
        selected.append(best)
        remaining.discard(best)
    return [(int(i), float(candidates[i].get("_score", 0.0))) for i in selected]
