"""Run the deterministic boundary cases for the Agent harness.

Normal completion and human confirmation are exercised with the real model via
``python -m app.agent_cli``.  This script covers parameter errors, tool
exceptions, tool timeouts, the step limit and the total-time limit.  The model
is scripted so each boundary can be triggered on purpose.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import repository  # noqa: E402
from app.agent.harness import AgentHarness, RunConfig  # noqa: E402
from app.agent.testing import (ScriptedModel, assistant_final, assistant_tool_call,  # noqa: E402
                               final_json, make_test_registry)
from app.agent.tools import execute_tool  # noqa: E402
from app.database import initialize_database  # noqa: E402


def _summary(name: str, result: dict) -> dict:
    return {
        "case": name,
        "run_id": result["run_id"],
        "status": result["status"],
        "error": result["error"],
        "steps": result["steps"],
        "tool_calls": result["tool_calls"],
        "elapsed_ms": result["elapsed_ms"],
        "success_check": result.get("success_check"),
        "final_answer": result.get("final_answer"),
        "log_path": result.get("log_path"),
        "tool_records": [{
            "name": row["name"],
            "status": row["status"],
            "error_type": row["error_type"],
            "message": row["message"],
            "confirmation": row["confirmation"],
        } for row in result["tool_call_records"]],
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    initialize_database()
    projects = repository.list_projects()
    if not projects:
        raise SystemExit("数据库中没有作品")
    project_id = projects[0]["id"]
    stats_result = execute_tool("compute_storyboard_stats", {"project_id": project_id},
                                {"run_id": "boundary-setup"})
    stats = stats_result["data"]
    final = final_json(stats, "节奏结论：镜头数量与总时长匹配目标，但时长分布均匀，建议增加长短镜头对比。")

    cases: list[dict] = []

    # 1. 参数类型错误：模型先传整数，校验失败后改为字符串再继续。
    model = ScriptedModel([
        assistant_tool_call("get_project_detail", {"project_id": 123}, "call_bad_type"),
        assistant_tool_call("get_project_detail", {"project_id": project_id}, "call_fixed_type"),
        assistant_tool_call("compute_storyboard_stats", {"project_id": project_id}, "call_stats_1"),
        assistant_final(final),
    ])
    result = AgentHarness(model_client=model, config=RunConfig(max_steps=6)).run(
        "分析作品分镜节奏。", project_id)
    cases.append(_summary("参数错误后纠正", result))

    # 2. 工具异常：测试工具抛出异常，错误回传后模型继续完成分析。
    model = ScriptedModel([
        assistant_tool_call("__test_boom", {}, "call_boom"),
        assistant_tool_call("compute_storyboard_stats", {"project_id": project_id}, "call_stats_2"),
        assistant_final(final),
    ])
    result = AgentHarness(model_client=model, registry=make_test_registry(),
                          config=RunConfig(max_steps=6)).run("分析作品分镜节奏。", project_id)
    cases.append(_summary("工具异常后继续", result))

    # 3. 工具超时：测试工具等待 1 秒，harness 只等 0.2 秒。
    model = ScriptedModel([
        assistant_tool_call("__test_slow", {}, "call_slow"),
        assistant_tool_call("compute_storyboard_stats", {"project_id": project_id}, "call_stats_3"),
        assistant_final(final),
    ])
    result = AgentHarness(model_client=model, registry=make_test_registry(),
                          config=RunConfig(max_steps=6, tool_timeout=0.2)).run(
        "分析作品分镜节奏。", project_id)
    cases.append(_summary("工具超时后继续", result))

    # 4. 达到最大步数：模型每次都请求工具，程序在第 2 步后停止。
    model = ScriptedModel([
        assistant_tool_call("list_projects", {"limit": 3}, "call_loop_1"),
        assistant_tool_call("list_projects", {"limit": 3}, "call_loop_2"),
        assistant_tool_call("list_projects", {"limit": 3}, "call_loop_3"),
    ])
    result = AgentHarness(model_client=model, config=RunConfig(max_steps=2)).run(
        "一直查询作品。", project_id)
    cases.append(_summary("达到最大步数", result))

    # 5. 总时限：模型调用耗时超过总时限，程序在调用返回后停止。
    model = ScriptedModel([assistant_final(final)], delay=0.2)
    result = AgentHarness(model_client=model,
                          config=RunConfig(total_timeout=0.05)).run(
        "分析作品分镜节奏。", project_id)
    cases.append(_summary("总时限停止", result))

    report = {
        "project_id": project_id,
        "project_title": projects[0]["title"],
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cases": cases,
    }
    target = ROOT / "docs" / "agent" / "samples" / "boundary.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n已保存：{target}")


if __name__ == "__main__":
    main()
