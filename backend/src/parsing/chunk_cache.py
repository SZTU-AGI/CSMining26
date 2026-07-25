"""农场证据 chunk 磁盘缓存。

动机：parse_case 每次 run 都要 Docling 重解析 9 份 docx/xlsx（云端 GPU 编码前的
CPU 大头开销），而同一 case 的源文件+切分参数不变时 chunk 是确定的 → 落盘缓存，
之后 run 直接读 jsonl，跳过文档解析。

缓存有效性：按「解析参数(parser/chunk_size/chunk_overlap) + 源文件指纹(名/大小/mtime)」
做 meta 校验。任一变化 → 缓存失效自动重建，绝不返回过期 chunk。

产物（每 case 两文件，放 data/chunk_cache/）：
- {case_id}.jsonl       每行一个 chunk dict（case_parser 原样输出）
- {case_id}.meta.json   {params, files:[{name,size,mtime}], n_chunks, built_at}

红线：只缓存证据文本 chunk（case_parser 的产物），不涉及 checkingpoints 红线内容。
"""
import os
import json
import time

from .case_parser import parse_case


def _source_fingerprint(case_dir: str):
    """采集 case 目录下所有文件的指纹（名/大小/mtime），按文件名排序。"""
    fp = []
    for f in sorted(os.listdir(case_dir)):
        p = os.path.join(case_dir, f)
        if not os.path.isfile(p):
            continue
        st = os.stat(p)
        fp.append({"name": f, "size": st.st_size, "mtime": int(st.st_mtime)})
    return fp


def _meta_matches(meta: dict, params: dict, files: list) -> bool:
    if not meta:
        return False
    if meta.get("params") != params:
        return False
    old = meta.get("files")
    if old != files:
        return False
    return True


def _load_chunks(jsonl_path: str):
    chunks = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def _write_cache(jsonl_path: str, meta_path: str, chunks: list, meta: dict):
    tmp = jsonl_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    os.replace(tmp, jsonl_path)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def parse_case_cached(case_dir: str, cache_dir: str, use_docling: bool = True,
                      chunk_size: int = 400, chunk_overlap: int = 50,
                      refresh: bool = False, verbose: bool = True):
    """带磁盘缓存的 parse_case。

    参数与 parse_case 一致，额外：
    - cache_dir: 缓存根目录（每 case 落 {case_id}.jsonl + .meta.json）
    - refresh:   True 则忽略现有缓存强制重解析并刷新
    返回: (chunks, hit)  —— hit=True 表示命中缓存，False 表示实解析。
    """
    case_id = os.path.basename(case_dir.rstrip("/\\"))
    os.makedirs(cache_dir, exist_ok=True)
    jsonl_path = os.path.join(cache_dir, f"{case_id}.jsonl")
    meta_path = os.path.join(cache_dir, f"{case_id}.meta.json")

    params = {
        "parser": "docling" if use_docling else "fallback",
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
    }
    files = _source_fingerprint(case_dir)

    if not refresh and os.path.isfile(jsonl_path) and os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = None
        if _meta_matches(meta, params, files):
            chunks = _load_chunks(jsonl_path)
            if verbose:
                print(f"[chunk_cache] HIT {case_id}: {len(chunks)} chunks (skip parse)")
            return chunks, True
        elif verbose:
            print(f"[chunk_cache] STALE {case_id}: params/source changed -> reparse")

    # miss / stale / refresh -> 实解析
    chunks = parse_case(case_dir, use_docling=use_docling,
                        chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    meta = {
        "case_id": case_id,
        "params": params,
        "files": files,
        "n_chunks": len(chunks),
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if chunks:
        _write_cache(jsonl_path, meta_path, chunks, meta)
        if verbose:
            print(f"[chunk_cache] BUILT {case_id}: {len(chunks)} chunks -> {jsonl_path}")
    elif verbose:
        print(f"[chunk_cache] EMPTY {case_id}: parse_case returned 0 chunk (not cached)")
    return chunks, False
