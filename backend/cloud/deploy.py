#!/usr/bin/env python3
"""FRECA 云端部署：paramiko 上传（修复 Windows->Linux 斜杠）+ 生成 config + setsid 后台启动。

连接参数从环境变量读取（避免在文件里硬编码密码）：
  CLOUD_HOST  (默认 223.109.239.30 移动)
  CLOUD_PORT  (默认 10624)
  CLOUD_USER  (默认 root)
  CLOUD_PASS  (必填)

用法:
  CLOUD_PASS='xxxx' CLOUD_HOST='223.109.239.30' python cloud/deploy.py
"""
import os
import sys
import time
import posixpath

import paramiko
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)            # backend 根
LOCAL_TASK2 = r"D:/桌面/农场任务二/Task2"
REMOTE_ROOT = "/root/freca"

SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", ".idea"}
SKIP_EXT = {".pyc"}


def connect():
    host = os.environ.get("CLOUD_HOST", "223.109.239.30")
    port = int(os.environ.get("CLOUD_PORT", "10624"))
    user = os.environ.get("CLOUD_USER", "root")
    pwd = os.environ.get("CLOUD_PASS", "")
    print(f"[deploy] connect {user}@{host}:{port} ...")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(hostname=host, port=port, username=user, password=pwd, timeout=30,
              look_for_keys=False, allow_agent=False)
    return c


def _mkdir_p(sftp, path):
    """递归创建远端目录（path 一律正斜杠）。"""
    parts = [p for p in path.split("/") if p]
    cur = ""
    for p in parts:
        cur = cur + "/" + p
        try:
            sftp.mkdir(cur)
        except IOError:
            pass


def put_dir(sftp, local, remote):
    """递归上传目录；远端路径一律用 posix 正斜杠（修复 Windows 反斜杠 bug）。"""
    for root, dirs, files in os.walk(local):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(root, local).replace(os.sep, "/")
        rdir = remote if rel in (".", "") else posixpath.join(remote, rel)
        _mkdir_p(sftp, rdir)
        for f in files:
            if os.path.splitext(f)[1] in SKIP_EXT:
                continue
            lf = os.path.join(root, f)
            rf = posixpath.join(rdir, f)
            sftp.put(lf, rf)


def make_cloud_config():
    sys.path.insert(0, BACKEND)
    from src.utils.io import load_config
    cfg = load_config()
    p = cfg["paths"]
    p["task2_root"] = f"{REMOTE_ROOT}/Task2"
    p["cases_dir"] = f"{REMOTE_ROOT}/Task2/SFRE_cases/SFRE_cases"
    p["rules_pdf"] = f"{REMOTE_ROOT}/Task2/1-Export Control (Plants and Plant Products)Rules 2021.pdf"
    p["submission_template"] = f"{REMOTE_ROOT}/Task2/submission_template.xlsx"
    p["checkingpoints"] = f"{REMOTE_ROOT}/Task2/checkingpoints_all_elements_onesheet.xlsx"
    p["code_root"] = f"{REMOTE_ROOT}/backend"
    p["index_dir"] = f"{REMOTE_ROOT}/backend/data/indexes"
    p["validation_dir"] = f"{REMOTE_ROOT}/backend/data/validation"
    p["submission_out"] = f"{REMOTE_ROOT}/backend/data/submission"
    p["rules_md"] = f"{REMOTE_ROOT}/backend/data/rules_raw.md"
    cfg["retrieval"]["use_fp16"] = True   # 云端 4090 必须 fp16
    return cfg


def main():
    c = connect()
    sftp = c.open_sftp()

    print("[deploy] 确认 GPU / 磁盘 ...")
    _, out, _ = c.exec_command("nvidia-smi --query-gpu=name,memory.total --format=csv 2>/dev/null; echo '---'; df -h / | tail -1")
    print(out.read().decode(errors="replace"))

    # 先杀掉远端可能还在跑的旧 setup/run 进程（避免 pip 锁冲突 & 旧的 BGE 下载）
    print("[deploy] 终止远端旧进程 ...")
    c.exec_command("pkill -f 'cloud/setup.sh' 2>/dev/null; "
                   "pkill -f 'cloud/run_retrieval.sh' 2>/dev/null; "
                   "pkill -f 'tests.test_bge_dense' 2>/dev/null; "
                   "pkill -f 'tests.test_qwen3_retrieval' 2>/dev/null; "
                   "pkill -f 'FlagEmbedding' 2>/dev/null; sleep 2; echo done")

    # 只清 backend（代码改了需覆盖）；Task2 数据若已存在则保留（SKIP_TASK2=1 跳过重传）
    print(f"[deploy] 清理旧 backend ...")
    _, o, _ = c.exec_command(f"rm -rf {REMOTE_ROOT}/backend")
    o.read()

    print(f"[deploy] 上传 backend -> {REMOTE_ROOT}/backend ...")
    t0 = time.time()
    put_dir(sftp, BACKEND, f"{REMOTE_ROOT}/backend")
    print(f"[deploy] backend 上传完成 ({time.time()-t0:.1f}s)")

    skip_task2 = os.environ.get("SKIP_TASK2", "") == "1"
    if skip_task2:
        print("[deploy] SKIP_TASK2=1，跳过 Task2 上传（假设云端已存在）")
    else:
        print(f"[deploy] 上传 Task2 -> {REMOTE_ROOT}/Task2 ...")
        t0 = time.time()
        put_dir(sftp, LOCAL_TASK2, f"{REMOTE_ROOT}/Task2")
        print(f"[deploy] Task2 上传完成 ({time.time()-t0:.1f}s)")

    # 写云端 config.yaml
    cfg = make_cloud_config()
    remote_cfg = f"{REMOTE_ROOT}/backend/config/config.yaml"
    with sftp.open(remote_cfg, "w") as fh:
        fh.write(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    print(f"[deploy] 云端 config 已写: {remote_cfg}")

    # setsid 启动，脱离 SSH 会话 -> 断开 SSH 不被 SIGHUP 杀掉
    cmd = (f"cd {REMOTE_ROOT}/backend && setsid bash -c "
           f"'bash cloud/setup.sh && echo SETUP_DONE && bash cloud/run_retrieval.sh' "
           f"> cloud/run.log 2>&1 < /dev/null &")
    print("[deploy] setsid 后台启动 setup + run_retrieval ...")
    c.exec_command(cmd)
    time.sleep(3)
    # 确认进程起来了
    _, out, _ = c.exec_command("pgrep -af 'cloud/setup.sh|cloud/run_retrieval.sh|run.log' | head")
    print("[deploy] 远端进程:", out.read().decode(errors="replace").strip() or "(未检测到，可能已退出)")
    sftp.close()
    c.close()
    print("[deploy] 完成。云端日志: tail -f /root/freca/backend/cloud/run.log")


if __name__ == "__main__":
    main()
