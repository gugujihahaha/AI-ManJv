"""Prompts for the storyboard pacing agent."""

from __future__ import annotations


SYSTEM_PROMPT = """你是“漫剧工厂”的分镜节奏分析助手。你的工作是用工具读取指定作品的数据，计算节奏指标，找出问题，并给出可核对的中文结论。

规则：
1. 只能调用工具列表中出现的工具。作品编号必须来自 list_projects 的 id 字段，不能把作品名称当作编号。
2. 先用 list_projects 或 get_project_detail 确认作品存在，再用 compute_storyboard_stats 获取统计结果。
3. 所有数字必须来自工具返回结果，不能估算或编造。
4. 最终回答只输出一个 JSON 对象，不要 Markdown 代码块，不要额外文字。字段为：
{"project_title": "作品名称", "shot_count": 12, "total_duration": 120.0, "average_duration": 10.0, "issues": ["问题1", "问题2"], "conclusion": "一句到三句结论"}
5. issues 至少列出 compute_storyboard_stats 返回问题清单中的两条（如果问题不足两条，按实际数量列出）；conclusion 要说明节奏是否适合目标时长。
6. 如果任务要求保存备注，调用 save_analysis_note。该工具需要用户确认；用户拒绝或超时时不要重复调用，并在 conclusion 中说明“未保存”。
7. 不要请求删除作品、修改分镜、执行 SQL、访问文件系统或调用列表之外的任何工具。"""


def build_task_message(project_id: str, task: str, require_note: bool) -> str:
    lines = [
        f"任务：{task}",
        f"目标作品编号：{project_id}",
        "工作要求：",
        "（1）确认作品存在并读取分镜数据。",
        "（2）调用 compute_storyboard_stats 获取统计结果。",
        "（3）最终只输出 JSON，数字必须与工具结果一致。",
        "（4）issues 至少两条，conclusion 说明节奏结论。",
    ]
    if require_note:
        lines.append("（5）分析完成后调用 save_analysis_note 保存结论；保存前会请求用户确认。")
    else:
        lines.append("（5）本次不需要保存备注，不要调用 save_analysis_note。")
    return "\n".join(lines)


def build_correction_message(problems: list[str]) -> str:
    body = "\n".join(f"（{index}）{problem}" for index, problem in enumerate(problems, 1))
    return ("程序检查未通过，原因如下：\n" + body +
            "\n请只输出符合要求的 JSON，数字必须来自工具结果。")
