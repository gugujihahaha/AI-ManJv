"""Turn the JSON evidence files into readable Markdown summaries."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "docs" / "agent" / "samples"


def _load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def _run_md(title: str, data: dict) -> str:
    lines = [
        f"# {title}",
        "",
        f"- 任务编号：{data['run_id']}",
        f"- 状态：{data['status']}",
        f"- 步数：{data['steps']}，模型调用：{data['model_calls']}，工具调用：{data['tool_calls']}",
        f"- 耗时：{data['elapsed_ms']} ms",
        f"- Token：输入 {data['usage']['prompt_tokens']} / 输出 {data['usage']['completion_tokens']} / 合计 {data['usage']['total_tokens']}",
        f"- 成功检查：{'通过' if (data.get('success_check') or {}).get('ok') else '未通过'}",
        "",
        "## 工具调用",
        "",
        "| 步骤 | 工具 | 状态 | 错误类型 | 确认 |",
        "|---|---|---|---|---|",
    ]
    for row in data["tool_call_records"]:
        confirmation = ""
        if row.get("confirmation"):
            confirmation = row["confirmation"].get("decision", "")
        lines.append(f"| {row['step']} | {row['name']} | {row['status']} | "
                     f"{row['error_type'] or '-'} | {confirmation or '-'} |")
    lines += ["", "## 最终回答", "", "```json", data.get("final_answer") or "（无）", "```", ""]
    return "\n".join(lines)


def main() -> None:
    probe = _load("tool_probe.json")
    lines = [
        "# 工具直接测试",
        "",
        f"- 作品：{probe.get('project_title', '')}（{probe.get('project_id', '')}）",
        "",
        "| 用例 | 工具 | 状态 | 错误类型 | 说明 |",
        "|---|---|---|---|---|",
    ]
    for case in probe["cases"]:
        result = case["result"]
        lines.append(f"| {case['label']} | {case['tool']} | {result['status']} | "
                     f"{result['error_type'] or '-'} | {result['message'] or '-'} |")
    (SAMPLES / "tool_probe.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    titles = {
        "run_normal.json": "正常完成运行",
        "run_confirm_approve.json": "人工确认同意运行",
        "run_confirm_deny.json": "人工确认拒绝运行",
    }
    for filename, title in titles.items():
        data = _load(filename)
        target = SAMPLES / filename.replace(".json", ".md")
        target.write_text(_run_md(title, data), encoding="utf-8")

    boundary = _load("boundary.json")
    lines = [
        "# 边界与异常测试",
        "",
        f"- 作品：{boundary.get('project_title', '')}（{boundary.get('project_id', '')}）",
        f"- 生成时间：{boundary.get('generated_at', '')}",
        "",
        "| 用例 | 状态 | 原因 | 步数 | 工具调用 | 关键工具状态 |",
        "|---|---|---|---|---|---|",
    ]
    for case in boundary["cases"]:
        statuses = ", ".join(f"{row['name']}={row['error_type'] or row['status']}"
                             for row in case["tool_records"]) or "-"
        lines.append(f"| {case['case']} | {case['status']} | {case['error'] or '-'} | "
                     f"{case['steps']} | {case['tool_calls']} | {statuses} |")
    (SAMPLES / "boundary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("已生成 tool_probe.md、run_normal.md、run_confirm_approve.md、run_confirm_deny.md、boundary.md")


if __name__ == "__main__":
    main()
