"""Scripted model and test-only tools for boundary cases.

These helpers are used by tests and by scripts/agent_boundary_tests.py.  They
never enter the production tool registry, so the normal Agent path still calls
the real model and the real tools.
"""

from __future__ import annotations

import json
import time

from app.agent.model import ModelError
from app.agent.tools import TOOL_SPECS, ToolSpec


def assistant_tool_call(name: str, arguments: dict, call_id: str = "call_test") -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }],
        },
        "model": "scripted-model",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "elapsed_ms": 0,
        "attempts": 1,
    }


def assistant_final(content: str) -> dict:
    return {
        "message": {"role": "assistant", "content": content},
        "model": "scripted-model",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "elapsed_ms": 0,
        "attempts": 1,
    }


def final_json(stats: dict, conclusion: str = "节奏结论：镜头时长分布均匀，适合目标时长。") -> str:
    issues = [item["message"] for item in stats.get("issues", [])]
    if not issues:
        issues = ["分镜结构完整，未发现明显问题"]
    return json.dumps({
        "project_title": stats["title"],
        "shot_count": stats["shot_count"],
        "total_duration": stats["total_duration"],
        "average_duration": stats["average_duration"],
        "issues": issues[:2],
        "conclusion": conclusion,
    }, ensure_ascii=False)


class ScriptedModel:
    def __init__(self, responses: list[dict], delay: float = 0.0) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict]] = []
        self.delay = delay

    def call(self, messages: list[dict], tools: list[dict] | None = None, **_kwargs) -> dict:
        if self.delay:
            time.sleep(self.delay)
        self.calls.append(messages)
        if not self.responses:
            raise ModelError("脚本化模型没有更多响应", "MODEL_SCRIPT_EXHAUSTED")
        return self.responses.pop(0)


def _boom(_args: dict, _context: dict) -> dict:
    raise RuntimeError("模拟工具异常：数据库连接被重置")


def _slow(_args: dict, _context: dict) -> dict:
    time.sleep(1.0)
    return {"status": "ok"}


def make_test_registry() -> dict[str, ToolSpec]:
    registry = dict(TOOL_SPECS)
    registry["__test_boom"] = ToolSpec(
        name="__test_boom",
        description="测试专用：抛出工具异常。",
        parameters={"type": "object", "properties": {}, "required": [],
                    "additionalProperties": False},
        handler=_boom,
        read_only=True,
        timeout_seconds=1.0,
    )
    registry["__test_slow"] = ToolSpec(
        name="__test_slow",
        description="测试专用：等待一秒，用于触发工具超时。",
        parameters={"type": "object", "properties": {}, "required": [],
                    "additionalProperties": False},
        handler=_slow,
        read_only=True,
        timeout_seconds=0.2,
    )
    return registry
