# -*- coding: utf-8 -*-
"""任务三 Pipeline 统一入口。

用法:
    python run.py cv        # 交叉验证评测(打印主/辅指标 + 逐类 F1)
    python run.py submit    # 全量训练 → 预测测试集 → 写 submission.csv
    python run.py all       # 先 cv 再 submit
"""
import sys


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "cv"
    if cmd in ("cv", "all"):
        import train; train.main()
    if cmd in ("submit", "all"):
        import predict; predict.main()
    if cmd not in ("cv", "submit", "all"):
        print(__doc__)


if __name__ == "__main__":
    main()
