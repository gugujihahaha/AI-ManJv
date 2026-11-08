"""Command line entry for the storyboard pacing agent.

Examples:
    python -m app.agent_cli --list-projects
    python -m app.agent_cli --tool-probe
    python -m app.agent_cli --project-id <id>
    python -m app.agent_cli --project-id <id> --require-note --confirm approve
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from app import repository
from app.agent.harness import AgentHarness, RunConfig
from app.agent.tools import execute_tool
from app.database import initialize_database


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _brief_tool_result(result: dict) -> dict:
    data = result.get("data")
    brief: dict = {
        "status": result.get("status"),
        "error_type": result.get("error_type", ""),
        "message": result.get("message", ""),
    }
    if isinstance(data, dict):
        if "projects" in data:
            brief["count"] = data.get("count")
            brief["projects"] = [{"id": row["id"], "title": row["title"]}
                                 for row in data.get("projects", [])[:3]]
        elif "shot_count" in data:
            brief["title"] = data.get("title")
            brief["shot_count"] = data.get("shot_count")
            brief["total_duration"] = data.get("total_duration")
            brief["issue_count"] = data.get("issue_count")
        elif "storyboard_count" in data:
            brief["title"] = data.get("title")
            brief["storyboard_count"] = data.get("storyboard_count")
    return brief


def run_tool_probe() -> dict:
    initialize_database()
    projects = repository.list_projects()
    if not projects:
        return {"error": "数据库中没有作品，请先创建样例作品"}
    project_id = projects[0]["id"]
    cases = []

    def case(label: str, tool: str, args: dict) -> None:
        started = time.perf_counter()
        result = execute_tool(tool, args, {"run_id": "tool-probe"})
        cases.append({
            "label": label,
            "tool": tool,
            "arguments": args,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "result": _brief_tool_result(result),
        })

    case("正确参数：列出作品", "list_projects", {"limit": 3})
    case("正确参数：读取作品与分镜", "get_project_detail", {"project_id": project_id})
    case("正确参数：计算分镜统计", "compute_storyboard_stats", {"project_id": project_id})
    case("错误参数：project_id 类型错误", "get_project_detail", {"project_id": 123})
    case("错误参数：缺少必填字段", "get_project_detail", {})
    case("错误参数：未知工具", "delete_project", {"project_id": project_id})
    case("错误参数：作品不存在", "get_project_detail", {"project_id": "not-exist"})
    return {"project_id": project_id, "project_title": projects[0]["title"], "cases": cases}


def _timed_input(prompt: str, timeout: float) -> bool:
    """Ask on Windows with a deadline; a missing answer counts as denial."""
    if os.name == "nt":
        import msvcrt

        print(prompt, end="", flush=True)
        started = time.monotonic()
        chars: list[str] = []
        while time.monotonic() - started < timeout:
            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in ("\r", "\n"):
                    print()
                    return "".join(chars).strip().lower() in {"y", "yes", "同意"}
                if char == "\x08":
                    if chars:
                        chars.pop()
                        print("\b \b", end="", flush=True)
                elif char == "\x03":
                    raise KeyboardInterrupt
                else:
                    chars.append(char)
                    print(char, end="", flush=True)
            time.sleep(0.05)
        print("\n确认等待超时，按拒绝处理。")
        return False
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes", "同意"}


def make_confirm_fn(mode: str, timeout: float):
    def confirm(request: dict) -> bool:
        print("\n===== 需要人工确认 =====")
        print(f"工具：{request['tool']}")
        print(f"作品：{request.get('project_title', '')}")
        print(f"操作：{request.get('impact', '')}")
        preview = request.get("summary_preview", "")
        if preview:
            print(f"摘要：{preview}")
        if mode == "approve":
            print("确认结果：已按 --confirm approve 自动同意")
            return True
        if mode == "deny":
            print("确认结果：已按 --confirm deny 自动拒绝")
            return False
        return _timed_input(f"确认执行？[y/N]（{timeout:.0f} 秒内回答）：", timeout)

    return confirm


def _print_run(result: dict, full_json: bool) -> None:
    print(f"任务编号：{result['run_id']}")
    print(f"状态：{result['status']}")
    if result.get("error"):
        print(f"原因：{result['error']}")
    print(f"模型调用：{result['model_calls']} 次；工具调用：{result['tool_calls']} 次；"
          f"步数：{result['steps']}")
    print(f"耗时：{result['elapsed_ms']} ms")
    print(f"日志：{result.get('log_path', '')}")
    check = result.get("success_check") or {}
    print(f"成功检查：{'通过' if check.get('ok') else '未通过'}")
    if check.get("problems"):
        for problem in check["problems"]:
            print(f"  - {problem}")
    print("最终回答：")
    print(result.get("final_answer") or "（无）")
    if full_json:
        print("\n===== 完整运行记录 =====")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="漫剧工厂分镜节奏分析 Agent")
    parser.add_argument("--list-projects", action="store_true", help="列出作品")
    parser.add_argument("--tool-probe", action="store_true", help="直接测试工具的正确与错误参数")
    parser.add_argument("--project-id", default="", help="目标作品编号")
    parser.add_argument("--task", default="分析该作品的分镜节奏与完整性问题，并给出结论。")
    parser.add_argument("--require-note", action="store_true", help="要求保存分析备注")
    parser.add_argument("--confirm", choices=["ask", "approve", "deny"], default="ask",
                        help="人工确认方式；ask 为交互式等待")
    parser.add_argument("--confirm-timeout", type=float, default=60.0)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--max-tool-calls", type=int, default=12)
    parser.add_argument("--total-timeout", type=float, default=90.0)
    parser.add_argument("--model-timeout", type=float, default=40.0)
    parser.add_argument("--tool-timeout", type=float, default=10.0)
    parser.add_argument("--save", default="", help="把运行记录保存为 JSON 文件")
    parser.add_argument("--json", action="store_true", help="打印完整运行记录")
    args = parser.parse_args(argv)

    if args.list_projects:
        initialize_database()
        rows = repository.list_projects()
        print(json.dumps([{
            "id": row["id"], "title": row["title"], "status": row["status"],
            "target_duration": row["target_duration"],
            "storyboard_count": row["storyboard_count"],
        } for row in rows], ensure_ascii=False, indent=2))
        return 0

    if args.tool_probe:
        report = run_tool_probe()
        text = json.dumps(report, ensure_ascii=False, indent=2)
        print(text)
        if args.save:
            target = Path(args.save)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            print(f"\n已保存：{target}")
        return 0

    if not args.project_id:
        print("请用 --project-id 指定作品编号，或先运行 --list-projects。")
        return 2

    config = RunConfig(
        max_steps=args.max_steps,
        max_tool_calls=args.max_tool_calls,
        total_timeout=args.total_timeout,
        model_timeout=args.model_timeout,
        tool_timeout=args.tool_timeout,
        confirm_timeout=args.confirm_timeout,
    )
    harness = AgentHarness(config=config,
                           confirm_fn=make_confirm_fn(args.confirm, args.confirm_timeout))
    result = harness.run(args.task, args.project_id, require_note=args.require_note)
    _print_run(result, args.json)
    if args.save:
        target = Path(args.save)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                          encoding="utf-8")
        print(f"\n已保存：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
