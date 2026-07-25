"""一次性抽取 checkingpoints row1(CP 定义, 红线允许) -> data/cp_definitions.yaml。
绝不抽取 row2/3(设立标准/法规映射, 推理禁用)。run.py 只依赖产物 yaml, 不碰红线 xlsx。
"""
import os, yaml
import openpyxl

SRC = r"D:/桌面/农场任务二/Task2/checkingpoints_all_elements_onesheet.xlsx"
OUT = r"D:/桌面/农场任务二/farm-case-analysis/backend/data/cp_definitions.yaml"

wb = openpyxl.load_workbook(SRC, read_only=True)
ws = wb.active
rows = list(ws.iter_rows(values_only=True))
ncols = max((len(r) for r in rows if r), default=0)

# row0 = Element 分组(稀疏, 跨列合并); row1 = CP 标题定义(稀疏)
row0 = rows[0]
row1 = rows[1]

def cell(r, c):
    return r[c] if c < len(r) else None

# Element 分组: 合并区间
elements = []
cur_el = None
for c in range(ncols):
    v = cell(row0, c)
    if v is not None:
        cur_el = str(v).strip()
    elements.append(cur_el)

# CP 标题: 合并单元格 -> 向前填充
titles = []
last = None
for c in range(ncols):
    v = cell(row1, c)
    if v is not None and str(v).strip():
        last = str(v).strip()
    titles.append(last)

defs = {}
for i in range(ncols):
    cp = f"CP{i+1}"
    defs[cp] = {
        "element": elements[i] or "",
        "title": titles[i] or f"(undefined CP{i+1})",
    }

header = (
    "# CP 定义(红线允许输入)。来源: checkingpoints_all_elements_onesheet.xlsx row1(CP 标题/定义)。\n"
    "# 仅作法规检索种子; 绝不添加 row2/3 设立标准/法规映射(推理禁用, 仅验证期作 GT)。\n"
    "# run.py 只读本文件, 不直接读红线 xlsx。\n"
)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", encoding="utf-8") as f:
    f.write(header)
    yaml.safe_dump({"cp_definitions": defs}, f, allow_unicode=True, sort_keys=False)

print(f"wrote {OUT}: {len(defs)} CPs")
for cp in ("CP1", "CP7", "CP8", "CP16", "CP17", "CP28", "CP29", "CP41"):
    print(f"  {cp}: [{defs[cp]['element']}] {defs[cp]['title']}")
