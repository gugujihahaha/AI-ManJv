"""DeepSeek API 最小调用脚本（实验 2.5：模型服务与 API 密钥安全）。

密钥管理：从项目根目录 .env 文件读取 DEEPSEEK_API_KEY（.env 已加入 .gitignore）。
运行前准备：
    1. 复制 .env.example 为 .env，填入真实 DEEPSEEK_API_KEY
    2. pip install openai
运行：
    python scripts/test_deepseek.py
输出：模型回复内容、请求时延、Token 用量。
"""

import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# 加载项目根目录下的 .env（密钥不进入代码和版本库）
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

api_key = os.getenv("DEEPSEEK_API_KEY")
if not api_key:
    raise SystemExit(
        "未读取到 DEEPSEEK_API_KEY：请复制 .env.example 为 .env 并填入真实密钥"
    )

client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

start = time.perf_counter()
response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {"role": "system", "content": "你是一个简洁的助手"},
        {"role": "user", "content": "用一句话介绍什么是漫画分镜"},
    ],
)
elapsed_ms = (time.perf_counter() - start) * 1000

print("=" * 50)
print("模型名称:", response.model)
print("运行位置: DeepSeek 云端 API")
print("请求类型: HTTPS POST /chat/completions（OpenAI 兼容协议）")
print("=" * 50)
print("返回结果:", response.choices[0].message.content)
print("=" * 50)
print(f"时延: {elapsed_ms:.0f} ms")
print(f"输入 Token: {response.usage.prompt_tokens}")
print(f"输出 Token: {response.usage.completion_tokens}")
print(f"总 Token:  {response.usage.total_tokens}")
