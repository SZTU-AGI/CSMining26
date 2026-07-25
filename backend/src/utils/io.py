"""IO / 配置辅助。"""
import os
import yaml


def load_config(path: str = "config/config.yaml") -> dict:
    """读取 config.yaml。path 可为相对后端根的路径或绝对路径。"""
    if not os.path.isabs(path):
        here = os.path.dirname(os.path.abspath(__file__))
        # src/utils/io.py -> 后端根 = 上溯两级
        root = os.path.dirname(os.path.dirname(here))
        path = os.path.join(root, path)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dir(p: str) -> str:
    os.makedirs(p, exist_ok=True)
    return p
