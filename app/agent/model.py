"""DeepSeek chat client that understands OpenAI-style tool calls."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from app.config import settings


class ModelError(RuntimeError):
    """A model call failed before a usable assistant message was returned."""

    def __init__(self, message: str, error_type: str = "MODEL_ERROR") -> None:
        super().__init__(message)
        self.error_type = error_type


class ModelClient:
    def __init__(self, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None) -> None:
        self.model = model or settings.deepseek_model
        self.api_key = api_key if api_key is not None else settings.deepseek_api_key
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")

    def call(self, messages: list[dict], tools: list[dict] | None = None, *,
             temperature: float = 0.2, max_tokens: int = 1200,
             timeout: float = 40.0, retries: int = 1) -> dict:
        if not self.api_key:
            raise ModelError("未配置 DEEPSEEK_API_KEY，无法调用模型", "MODEL_CONFIG")
        body: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        url = f"{self.base_url}/chat/completions"
        last_error: ModelError | None = None

        for attempt in range(retries + 1):
            request = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                choice = data["choices"][0]
                message = choice["message"]
                if not isinstance(message, dict):
                    raise ModelError("模型返回的 message 不是对象", "MODEL_STRUCTURE")
                tool_calls = message.get("tool_calls") or []
                if tool_calls and not isinstance(tool_calls, list):
                    raise ModelError("模型返回的 tool_calls 不是数组", "MODEL_STRUCTURE")
                content = message.get("content")
                if content is not None and not isinstance(content, str):
                    raise ModelError("模型返回的 content 不是字符串", "MODEL_STRUCTURE")
                usage = data.get("usage") or {}
                return {
                    "message": message,
                    "model": data.get("model", self.model),
                    "finish_reason": choice.get("finish_reason", ""),
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    },
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "attempts": attempt + 1,
                }
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
                last_error = ModelError(f"接口返回错误（{exc.code}）{detail}", "MODEL_HTTP")
                if exc.code in {429, 500, 502, 503, 504} and attempt < retries:
                    time.sleep(1)
                    continue
                raise last_error from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = ModelError(f"连接失败或超时：{exc}", "MODEL_TIMEOUT")
                if attempt < retries:
                    time.sleep(1)
                    continue
                raise last_error from exc
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise ModelError("模型返回结构异常", "MODEL_STRUCTURE") from exc
        raise last_error or ModelError("模型调用失败")
