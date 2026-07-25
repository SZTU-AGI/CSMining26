"""云端：huggingface_hub 列文件 -> modelscope 直链 -> aria2c 多文件并行下载。

为什么这样：
- Qwen3 在 huggingface.co 强制走 xet CAS 桥（云端 403），huggingface_hub 库+DISABLE_XET 能绕但单连接且 model-00001 卡死。
- modelscope 直链 (modelscope.cn/.../resolve/master/) 是干净 LFS（实测 206 秒回、不走 xet），稳定可用。
- aria2c -j16 多文件并行：两个大 safetensors 分片同时各拉一路，整体 ~2x 带宽；-c 断点续传。

输出到 local_dir 原始文件名，加载器用 QWEN3_EMBEDDING_PATH / QWEN3_RERANKER_PATH 直接加载。
"""
import os
import subprocess
from huggingface_hub import HfApi

OUT = "/root/freca/models"
REPOS = [
    ("Qwen3-Embedding-4B", "Qwen/Qwen3-Embedding-4B"),
    ("Qwen3-Reranker-4B", "Qwen/Qwen3-Reranker-4B"),
]

api = HfApi()
lines = []
for name, repo in REPOS:
    info = api.model_info(repo, revision="main")
    for s in info.siblings:
        fn = s.rfilename
        url = "https://modelscope.cn/models/%s/resolve/master/%s" % (repo, fn)
        lines.append(url)
        # aria2c input 文件的选项行必须以空白开头，否则会被当成 URI
        lines.append(" out=" + name + "/" + fn)

with open("/tmp/aria2_list.txt", "w") as f:
    f.write(chr(10).join(lines))

# -x16 单文件多连接尝试；-j16 多文件并行（两个大分片同时各一路）；-c 断点续传
r = subprocess.run(
    ["aria2c", "-x16", "-s16", "-k1M", "-j16", "-c",
     "--dir", OUT, "-i", "/tmp/aria2_list.txt"],
    check=True,
)
print("[hf] 🎉 ALL MODELS DOWNLOADED", flush=True)
