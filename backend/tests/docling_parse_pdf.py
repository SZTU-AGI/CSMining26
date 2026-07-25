"""Docling 解析法规 PDF → 结构化 Markdown，存盘供条款切分分析。"""
import time, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.utils.io import load_config

t0 = time.time()
from docling.document_converter import DocumentConverter

cfg = load_config()
pdf = cfg["paths"]["rules_pdf"]
out = os.path.join(cfg["paths"]["code_root"], "data", "rules_raw.md")

print(f"开始解析: {pdf}")
res = DocumentConverter().convert(pdf)
md = res.document.export_to_markdown()
with open(out, "w", encoding="utf-8") as f:
    f.write(md)
print(f"完成 {time.time()-t0:.1f}s | md len={len(md)} | saved: {out}")
