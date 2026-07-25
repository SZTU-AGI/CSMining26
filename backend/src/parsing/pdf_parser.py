"""法规 PDF 解析 → 按条款(clause)切分。

优先 Docling（表格/结构保真更好），失败降级 pdfplumber。
⚠️ 仅处理法规 PDF；绝不读取 checkingpoints 表。

切分标准(Docling 主路径，基于 Docling Markdown 结构化输出):
  主单元 = X-Y 编号 section（如 "2-4 Export of prescribed plants..."）
  section 内遇 H2 子标题(非编号)则拆为子 chunk，text 前置父条款头(clause_id + title)
  超 regulation_max_size 的 chunk 走 _apply_size_limit 兜底(列表项/句子硬切带父头)
  短条款不合并（避免不同规定混入同一 chunk，引用错位）
  统计依据: 178 个 X-Y section, P50=800/P90=2074/max=19731; 按子标题优先拆后
            P50=677, 上限2000时仅7%超限, 配合兜底即可。
"""
import os
import re
from .fallback import fallback_parse_pdf

# X-Y 编号 section 标题
_SEC_XY = re.compile(r"^(#{1,6})\s+(\d+-\d+)\s+(.*?)\s*$", re.M)
# 容器标题 Chapter/Part/Division
_RE_CONTAINER = re.compile(r"^(#{1,6})\s+(Chapter|Part|Division)\s", re.M)
# 老式页眉 "Section X-Y"（PDF 每页重复，忽略）
_RE_OLDSEC = re.compile(r"^(#{1,6})\s+Section\s+\d+-\d+", re.M)
# 任何 Markdown 标题
_RE_ANYHEAD = re.compile(r"^#{1,6}\s+")
# 子项标记：(a) (b) (1) (i) 等
_SUBITEM_RE = re.compile(r"(?m)^\s*\(([a-z0-9]+[ivxlcdm]*)\)\s+", re.IGNORECASE)


def _split_into_clauses(md_text: str, max_size: int = 2000):
    """Docling Markdown 按结构切分法规条款。详见模块 docstring。"""
    lines = md_text.split("\n")
    raw = []
    cur_sec = None
    cur_sub = None
    sub_cnt = 0

    def flush_sub():
        nonlocal cur_sub
        if cur_sub is not None:
            raw.append(cur_sub)
            cur_sub = None

    def flush_sec():
        nonlocal cur_sec
        flush_sub()
        if cur_sec is not None:
            raw.append(cur_sec)
            cur_sec = None

    for l in lines:
        m = _SEC_XY.match(l)
        if m:
            flush_sec()
            cur_sec = {
                "clause_id": m.group(2),
                "title": m.group(3).strip(),
                "text": "",
            }
            sub_cnt = 0
            continue
        if _RE_CONTAINER.match(l) or _RE_OLDSEC.match(l):
            continue  # 容器/老式页眉跳过
        if _RE_ANYHEAD.match(l):
            # section 内子标题(非编号) → 拆子 chunk
            if cur_sec is None:
                continue
            flush_sub()
            sub_cnt += 1
            t = re.match(r"^#{1,6}\s+(.*?)\s*$", l).group(1)
            cur_sub = {
                "clause_id": f"{cur_sec['clause_id']}-{sub_cnt}",
                "title": t,
                "text": f"{cur_sec['clause_id']} {cur_sec['title']}\n",
            }
            continue
        # 普通文本行
        if cur_sub is not None:
            cur_sub["text"] += l + "\n"
        elif cur_sec is not None:
            cur_sec["text"] += l + "\n"
    flush_sec()

    raw = [c for c in raw if c["text"].strip()]
    out = []
    for c in raw:
        if len(c["text"]) <= max_size:
            out.append(c)
        else:
            out.extend(_apply_size_limit([c], max_size))
    return out


def _apply_size_limit(clauses, max_size: int = 2000):
    """超长 chunk 兜底：按子项(a)(b)/(1)→段落→句子硬切，每块带父条款头与唯一后缀。

    关键：每个拆出段必须有唯一 clause_id（父id + 后缀），否则多个 chunk 共享
    同一 id 会破坏检索索引。子项用原编号(a/b/1)，段落用 -p0/-p1，句子用 -s0/-s1。
    """
    out = []
    for c in clauses:
        text = c["text"]
        header = f"{c['clause_id']} {c['title']}"
        if len(text) <= max_size:
            out.append(c)
            continue
        subs = list(_SUBITEM_RE.finditer(text))
        if len(subs) >= 2:
            units = []
            for i, m in enumerate(subs):
                s = m.start()
                e = subs[i + 1].start() if i + 1 < len(subs) else len(text)
                seg = text[s:e].strip()
                if seg:
                    units.append((f"{c['clause_id']}-{m.group(1)}", seg))
        else:
            paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
            units = [(f"{c['clause_id']}-p{i}", p) for i, p in enumerate(paras)] if paras \
                else [(f"{c['clause_id']}-p0", text)]
        for uid, seg in units:
            if len(header) + len(seg) + 1 <= max_size:
                out.append({"clause_id": uid, "title": c["title"], "text": f"{header}\n{seg}"})
            else:
                sents = [x for x in re.split(r"(?<=[.!?])\s+|\n+", seg) if x.strip()]
                cur = header
                si = 0
                for s in sents:
                    if len(cur) + len(s) + 1 <= max_size:
                        cur = cur + " " + s
                    else:
                        out.append({"clause_id": f"{uid}-s{si}", "title": c["title"], "text": cur})
                        si += 1
                        cur = header + " " + s
                if cur != header:
                    out.append({"clause_id": f"{uid}-s{si}", "title": c["title"], "text": cur})
    return out


def _split_into_clauses_legacy(text: str):
    """降级(pdfplumber 纯文本)切分：旧数字编号正则。仅 fallback 用。"""
    _CLAUSE_RE = re.compile(r"(?m)(?:^|\n)\s*(\d+(?:\.\d+)*)\s+([A-Z][^\n]{0,120})")
    matches = list(_CLAUSE_RE.finditer(text))
    clauses = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        clauses.append({
            "clause_id": m.group(1),
            "title": m.group(2).strip(),
            "text": text[start:end].strip(),
        })
    if not clauses:
        clauses.append({"clause_id": "0", "title": "full", "text": text.strip()})
    return clauses


def parse_rules(pdf_path: str, use_docling: bool = True, max_size: int = 2000,
                cache_md: str = None):
    """返回 [{clause_id, title, text}]。来源仅法规 PDF，不碰 checkingpoints 表。

    use_docling=True 时优先读 cache_md（已生成的 Docling Markdown，避免每次重跑 7 分钟）；
    缺失则现场转换并写回 cache_md。失败落到降级 pdfplumber。
    """
    if use_docling:
        md = None
        if cache_md and os.path.isfile(cache_md):
            md = open(cache_md, encoding="utf-8").read()
        else:
            try:
                from docling.document_converter import DocumentConverter
                res = DocumentConverter().convert(pdf_path)
                md = res.document.export_to_markdown()
                if cache_md:
                    os.makedirs(os.path.dirname(cache_md), exist_ok=True)
                    open(cache_md, "w", encoding="utf-8").write(md)
            except Exception:
                md = None
        if md:
            return _split_into_clauses(md, max_size)
    # 降级
    text = fallback_parse_pdf(pdf_path)
    return _apply_size_limit(_split_into_clauses_legacy(text), max_size)
