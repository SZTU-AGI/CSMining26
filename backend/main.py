"""FRECA 后端入口。

用法:
  python main.py                       # 全量 4100 判决(需 DEEPSEEK_API_KEY + 云端 Qwen3 权重)
  python main.py --cases 1             # 冒烟: 只跑第 1 个 case 的全部 CP
  python main.py --cps 1,2,3           # 只跑指定 CP
  python main.py --dry-run-retrieval   # 只做法规 grounding+证据检索, 不调 LLM(验证检索质量)
  python main.py --estimate-only       # 仅打印成本估算
  python main.py --no-resume           # 从头开始(忽略已完成进度)

所有文本 LLM 调用统一走 src/llm/auditor.DeepSeekAuditor (deepseek-v4-flash, temp=0)。
向量检索/重排为 Qwen3 本地模型, 与 LLM 接口物理隔离。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 自动加载 .env 到环境变量(若存在)。load_dotenv 默认不覆盖已 export 的变量,
# 因此云端 run_retrieval.sh 显式 export 的 DEEPSEEK_API_KEY / QWEN3_* 优先, 安全。
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.pipeline.run import main as run_main


if __name__ == "__main__":
    run_main()
