"""文本切片：按句子/换行边界切分为 chunk。"""
import re


def chunk_text(text, size: int = 1600, overlap: int = 160):
    """将文本切分为 <= size 字符的 chunk（size 单位=字符数，非 token；≈4字符/token）。

    优先在句子边界（.!?）或换行处切断，避免截断半句。
    ⚠️ overlap 当前未启用：按整句累加不切断，边界丢失风险低；
    若验证集评估发现边界丢信息，再实现句级 overlap。
    """
    if not text or not text.strip():
        return []
    sents = re.split(r"(?<=[.!?])\s+|\n+", text)
    chunks, cur = [], ""
    for s in sents:
        if len(cur) + len(s) + 1 <= size:
            cur = (cur + " " + s).strip()
        else:
            if cur:
                chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    return chunks
