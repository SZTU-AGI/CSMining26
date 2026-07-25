"""DeepSeek LLM 判决。固定 model + temperature=0，保证可复现。"""
import os
import requests
from .prompt import EXACT_PROMPT


class DeepSeekAuditor:
    """DeepSeek V4 判决客户端。

    - 模型固定 deepseek-v4-flash（旧 deepseek-chat 2026-07-24 停用）。
    - 非思考模式(thinking=disabled)：单 token 判决更快/省/确定。
    - temperature=0：可复现。
    - API Key 只从环境变量 DEEPSEEK_API_KEY 读，禁止 hard-code。
    """

    def __init__(self, model: str = "deepseek-v4-flash", temperature: int = 0,
                 api_key: str = None, api_base: str = "https://api.deepseek.com",
                 max_retries: int = 3, thinking_mode: str = "disabled",
                 timeout: int = 60):
        self.model = model
        self.temperature = temperature
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        self.api_base = api_base.rstrip("/")
        self.max_retries = max_retries
        self.thinking_mode = thinking_mode
        self.timeout = timeout

    def audit(self, policy_excerpts: list, evidence_excerpts: list, perspective=None, max_retries=None):
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY 未设置")
        policy = "\n\n".join(f"[{i+1}] {p}" for i, p in enumerate(policy_excerpts))
        evidence = "\n\n".join(f"[{i+1}] {e}" for i, e in enumerate(evidence_excerpts))
        user = (
            f"=== Policy excerpts (Export Control Rules 2021) ===\n{policy}\n\n"
            f"=== Farm evidence ===\n{evidence}\n\n"
            "Return exactly one token (1 / 0 / N/A):"
        )
        system = EXACT_PROMPT
        if perspective:
            # 自检/验证器用的"视角"指令，附加到固定 EXACT_PROMPT 之后（主判决 EXACT_PROMPT 不变，可复现）。
            # perspective 由代码生成（见 prompt.py 的 *_PERSPECTIVE 常量），绝不引用 checkingpoints 红线表。
            system = EXACT_PROMPT + "\n\n" + perspective
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "thinking": {"type": self.thinking_mode},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        retries = max_retries or self.max_retries
        for _ in range(retries):
            try:
                r = requests.post(
                    f"{self.api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                    timeout=self.timeout,
                )
                r.raise_for_status()
                data = r.json()
                tok = data["choices"][0]["message"]["content"].strip()
                usage = data.get("usage", {})
                # 返回 (verdict, usage)：调用方累加 token 用于计费/审计日志（见 CODE_STANDARD §9.3/§9.4）
                return self._parse(tok), usage
            except Exception:
                continue
        return None, {}

    @staticmethod
    def _parse(tok: str):
        t = tok.strip().upper()
        if t.startswith("1"):
            return 1
        if t.startswith("0"):
            return 0
        if "N/A" in t or t == "NA":
            return "N/A"
        return None
