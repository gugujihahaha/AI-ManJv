"""Exercise the three failure paths of app.rag.call_model.

Run from the project root:
    python scripts/rag_failure_test.py

The .env file is not modified; a temporary Settings copy is assigned to the
rag module and restored before the process exits.
"""

from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.rag as rag  # noqa: E402


CASES = [
    ("空密钥（未配置接口）", replace(rag.settings, deepseek_api_key=""), 30, 0),
    ("错误密钥（接口返回 401）", replace(rag.settings, deepseek_api_key="sk-invalid-test-key"), 30, 0),
    ("连接失败（本机空端口）", replace(rag.settings, deepseek_base_url="http://127.0.0.1:9"), 5, 0),
    ("请求超时（不可达测试网段）", replace(rag.settings, deepseek_base_url="http://192.0.2.1:81"), 3, 0),
]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    lines = ["RAG 模型调用失败测试", f"运行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    original = rag.settings
    try:
        for name, temporary, timeout, retries in CASES:
            rag.settings = temporary
            started = time.perf_counter()
            try:
                rag.call_model(
                    [{"role": "user", "content": "只回复两个字：你好"}],
                    max_tokens=8,
                    timeout=timeout,
                    retries=retries,
                )
                result = "未按预期失败"
            except rag.RagError as exc:
                result = f"RagError：{exc}"
            except Exception as exc:  # noqa: BLE001 - show unexpected exceptions too
                result = f"{type(exc).__name__}：{exc}"
            elapsed = int((time.perf_counter() - started) * 1000)
            line = f"{name} -> {result}（{elapsed} ms）"
            print(line)
            lines.append(line)
    finally:
        rag.settings = original
        target = ROOT / "docs" / "rag" / "samples" / "failure_test.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已保存：{target}")


if __name__ == "__main__":
    main()
