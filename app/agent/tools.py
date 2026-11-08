"""Tool schemas, program-side validation and the only execution entry.

The model never names a Python function directly.  It can only ask for a tool
that is present in the registry below; the harness validates the argument
types, required fields and access scope before the handler runs.
"""

from __future__ import annotations

import json
import re
import statistics
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from app import repository


PROJECT_ID_PATTERN = r"^[A-Za-z0-9-]{1,64}$"


class ToolValidationError(ValueError):
    """The model supplied arguments that do not match the schema."""


class ToolAccessError(PermissionError):
    """The requested project or data scope is not available."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, dict], Any]
    read_only: bool = True
    requires_confirmation: bool = False
    timeout_seconds: float = 10.0
    impact: str = ""

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _object_schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _project_id_property() -> dict:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": 64,
        "pattern": PROJECT_ID_PATTERN,
        "description": "作品编号，来自 list_projects 的 id 字段",
    }


def _project_or_access_error(project_id: str) -> dict:
    project = repository.get_project(project_id)
    if not project:
        raise ToolAccessError(f"作品不存在或无权访问：{project_id}")
    return project


def _clip(text: str, limit: int) -> tuple[str, bool]:
    text = text or ""
    if len(text) <= limit:
        return text, False
    return text[:limit] + "…", True


def _list_projects(args: dict, _context: dict) -> dict:
    limit = int(args.get("limit", 20))
    rows = repository.list_projects()[:limit]
    projects = [{
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "target_duration": row["target_duration"],
        "storyboard_count": row["storyboard_count"],
    } for row in rows]
    return {"count": len(projects), "projects": projects}


def _get_project_detail(args: dict, _context: dict) -> dict:
    project = _project_or_access_error(args["project_id"])
    storyboards: list[dict] = []
    truncated = False
    for row in project["storyboards"]:
        scene, scene_cut = _clip(row["scene_description"], 160)
        dialogue, dialogue_cut = _clip(row["dialogue"], 120)
        truncated = truncated or scene_cut or dialogue_cut
        storyboards.append({
            "sequence": row["sequence"],
            "scene_description": scene,
            "dialogue": dialogue,
            "emotion": row["emotion"],
            "duration": row["duration"],
            "status": row["status"],
        })
    return {
        "project_id": project["id"],
        "title": project["title"],
        "art_style": project["art_style"],
        "voice": project["voice"],
        "target_duration": project["target_duration"],
        "status": project["status"],
        "storyboard_count": len(storyboards),
        "storyboards": storyboards,
        "truncated": truncated,
    }


def _compute_storyboard_stats(args: dict, _context: dict) -> dict:
    project = _project_or_access_error(args["project_id"])
    rows = project["storyboards"]
    count = len(rows)
    durations = [float(row["duration"]) for row in rows]
    total = round(sum(durations), 2)
    average = round(total / count, 2) if count else 0.0
    deviation = round(statistics.pstdev(durations), 2) if count > 1 else 0.0
    dialogue_count = sum(1 for row in rows if row["dialogue"].strip())
    dialogue_coverage = round(dialogue_count / count, 3) if count else 0.0

    emotion_counts: dict[str, int] = {}
    for row in rows:
        emotion = row["emotion"].strip() or "未填写"
        emotion_counts[emotion] = emotion_counts.get(emotion, 0) + 1

    histogram: dict[str, int] = {}
    for value in durations:
        key = f"{value:.1f}"
        histogram[key] = histogram.get(key, 0) + 1

    issues: list[dict] = []
    if not rows:
        issues.append({"code": "EMPTY_STORYBOARDS", "message": "作品还没有分镜，无法分析节奏"})
    else:
        sequences = [int(row["sequence"]) for row in rows]
        if sequences != list(range(1, count + 1)):
            issues.append({
                "code": "SEQUENCE_GAP",
                "message": f"分镜序号不是从 1 连续排列，实际为 {sequences}",
            })

        out_of_range = [int(row["sequence"]) for row in rows
                        if row["duration"] < 1 or row["duration"] > 30]
        if out_of_range:
            issues.append({
                "code": "DURATION_OUT_OF_RANGE",
                "message": f"有 {len(out_of_range)} 个镜头时长超出 1 到 30 秒：序号 {out_of_range}",
            })

        if count >= 3 and max(durations) - min(durations) < 0.5:
            issues.append({
                "code": "UNIFORM_DURATION",
                "message": f"{count} 个镜头的时长全部为 {durations[0]:.1f} 秒，节奏没有变化",
            })

        missing_dialogue = [int(row["sequence"]) for row in rows if not row["dialogue"].strip()]
        if missing_dialogue:
            issues.append({
                "code": "DIALOGUE_MISSING",
                "message": f"有 {len(missing_dialogue)} 个镜头没有台词或旁白：序号 {missing_dialogue}",
            })

        unconfirmed_emotion = [int(row["sequence"]) for row in rows
                               if row["emotion"].strip() == "待确认"]
        if unconfirmed_emotion:
            issues.append({
                "code": "EMOTION_UNCONFIRMED",
                "message": f"有 {len(unconfirmed_emotion)} 个镜头的情绪仍为“待确认”",
            })

        target = int(project["target_duration"])
        if target > 0 and abs(total - target) / target > 0.2:
            gap = round((total - target) / target * 100, 1)
            issues.append({
                "code": "TOTAL_DURATION_DEVIATION",
                "message": f"总时长 {total:.1f} 秒与目标 {target} 秒相差 {gap:+.1f}%",
            })

        not_confirmed = [int(row["sequence"]) for row in rows if row["status"] != "confirmed"]
        if not_confirmed:
            issues.append({
                "code": "STATUS_NOT_CONFIRMED",
                "message": f"有 {len(not_confirmed)} 个镜头尚未确认：序号 {not_confirmed}",
            })

    return {
        "project_id": project["id"],
        "title": project["title"],
        "target_duration": int(project["target_duration"]),
        "shot_count": count,
        "total_duration": total,
        "average_duration": average,
        "min_duration": round(min(durations), 2) if durations else 0.0,
        "max_duration": round(max(durations), 2) if durations else 0.0,
        "duration_std": deviation,
        "duration_histogram": histogram,
        "dialogue_count": dialogue_count,
        "dialogue_coverage": dialogue_coverage,
        "emotion_counts": emotion_counts,
        "issue_count": len(issues),
        "issues": issues,
    }


def _save_analysis_note(args: dict, context: dict) -> dict:
    project = _project_or_access_error(args["project_id"])
    summary = args["summary"].strip()
    note = repository.save_analysis_note(project["id"], context["run_id"], summary)
    return {
        "status": "saved",
        "note_id": note["id"],
        "project_id": project["id"],
        "run_id": note["run_id"],
        "summary_chars": len(summary),
        "summary_preview": summary[:60],
    }


TOOL_SPECS: dict[str, ToolSpec] = {
    "list_projects": ToolSpec(
        name="list_projects",
        description="列出当前可访问的漫剧作品，返回编号、名称、状态、目标时长与分镜数量。",
        parameters=_object_schema({
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 20,
                "description": "最多返回多少个作品",
            },
        }, []),
        handler=_list_projects,
        read_only=True,
        timeout_seconds=5.0,
    ),
    "get_project_detail": ToolSpec(
        name="get_project_detail",
        description="读取一个作品的基本信息与全部分镜。长文本会截断到固定长度，仅用于了解内容。",
        parameters=_object_schema({"project_id": _project_id_property()}, ["project_id"]),
        handler=_get_project_detail,
        read_only=True,
        timeout_seconds=5.0,
    ),
    "compute_storyboard_stats": ToolSpec(
        name="compute_storyboard_stats",
        description="计算一个作品的分镜数量、总时长、平均时长、对白覆盖率、情绪分布与问题清单。",
        parameters=_object_schema({"project_id": _project_id_property()}, ["project_id"]),
        handler=_compute_storyboard_stats,
        read_only=True,
        timeout_seconds=5.0,
    ),
    "save_analysis_note": ToolSpec(
        name="save_analysis_note",
        description="把一个作品的分析结论保存为备注。该工具会修改数据库，执行前需要用户确认。",
        parameters=_object_schema({
            "project_id": _project_id_property(),
            "summary": {
                "type": "string",
                "minLength": 10,
                "maxLength": 800,
                "description": "要保存的分析结论，10 到 800 字",
            },
        }, ["project_id", "summary"]),
        handler=_save_analysis_note,
        read_only=False,
        requires_confirmation=True,
        timeout_seconds=5.0,
        impact="在 analysis_notes 表新增或更新一条分析备注，不修改分镜、作品状态、剧情文本与图片。",
    ),
}


def get_tool_definitions(registry: dict[str, ToolSpec] | None = None) -> list[dict]:
    registry = registry or TOOL_SPECS
    return [spec.to_openai() for spec in registry.values()]


def parse_tool_arguments(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if raw is None or raw == "":
        return {}
    if not isinstance(raw, str):
        raise ToolValidationError("工具参数必须是 JSON 对象")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolValidationError(f"工具参数不是合法 JSON：{exc.msg}") from exc
    if not isinstance(value, dict):
        raise ToolValidationError("工具参数必须是 JSON 对象")
    return value


def _validate_value(field: str, value: Any, schema: dict) -> None:
    expected = schema.get("type")
    if expected == "string":
        if not isinstance(value, str):
            raise ToolValidationError(f"字段 {field} 应为字符串，实际为 {type(value).__name__}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ToolValidationError(f"字段 {field} 至少需要 {schema['minLength']} 个字符")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ToolValidationError(f"字段 {field} 不能超过 {schema['maxLength']} 个字符")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ToolValidationError(f"字段 {field} 的格式不符合要求")
    elif expected == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolValidationError(f"字段 {field} 应为整数，实际为 {type(value).__name__}")
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolValidationError(f"字段 {field} 应为数字，实际为 {type(value).__name__}")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise ToolValidationError(f"字段 {field} 应为布尔值")
    elif expected == "array":
        if not isinstance(value, list):
            raise ToolValidationError(f"字段 {field} 应为数组")
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                _validate_value(f"{field}[{index}]", item, item_schema)
    elif expected == "object":
        if not isinstance(value, dict):
            raise ToolValidationError(f"字段 {field} 应为对象")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolValidationError(f"字段 {field} 不能小于 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolValidationError(f"字段 {field} 不能大于 {schema['maximum']}")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolValidationError(f"字段 {field} 只能是 {schema['enum']} 之一")


def validate_arguments(spec: ToolSpec, args: dict) -> None:
    if not isinstance(args, dict):
        raise ToolValidationError("工具参数必须是 JSON 对象")
    properties = spec.parameters.get("properties", {})
    required = spec.parameters.get("required", [])
    unknown = sorted(set(args) - set(properties))
    if unknown:
        raise ToolValidationError(f"工具 {spec.name} 不接受字段：{', '.join(unknown)}")
    for name in required:
        if name not in args:
            raise ToolValidationError(f"工具 {spec.name} 缺少必填字段：{name}")
    for name, value in args.items():
        _validate_value(name, value, properties[name])


def execute_tool(name: str, args: dict, context: dict,
                 registry: dict[str, ToolSpec] | None = None,
                 timeout: float | None = None) -> dict:
    """Validate and run one tool in a worker thread.

    A timeout only stops this process from waiting.  The worker thread cannot be
    killed safely, so the result is marked ``timeout`` and a mutating tool is
    additionally marked ``mutating`` for the harness to treat its state as
    unknown.
    """
    registry = registry or TOOL_SPECS
    spec = registry.get(name)
    if spec is None:
        return {
            "status": "error",
            "error_type": "UNKNOWN_TOOL",
            "message": f"工具 {name} 不在允许列表中",
        }
    try:
        validate_arguments(spec, args)
    except ToolValidationError as exc:
        return {"status": "error", "error_type": "VALIDATION_ERROR", "message": str(exc)}

    box: dict[str, Any] = {}
    started = time.perf_counter()

    def worker() -> None:
        try:
            box["data"] = spec.handler(args, context)
        except ToolAccessError as exc:
            box["error"] = ("ACCESS_DENIED", str(exc))
        except Exception as exc:  # noqa: BLE001 - the model receives the error type
            box["error"] = ("TOOL_EXCEPTION", f"{type(exc).__name__}: {exc}")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout if timeout is not None else spec.timeout_seconds)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if thread.is_alive():
        return {
            "status": "timeout",
            "error_type": "TOOL_TIMEOUT",
            "message": f"工具 {name} 超过等待时间仍未返回；本次已停止等待，后台线程可能仍在运行",
            "elapsed_ms": elapsed_ms,
            "mutating": not spec.read_only,
        }
    if "error" in box:
        error_type, message = box["error"]
        return {"status": "error", "error_type": error_type, "message": message,
                "elapsed_ms": elapsed_ms}
    return {"status": "ok", "data": box.get("data"), "elapsed_ms": elapsed_ms}


def project_scope_ok(project_id: str) -> bool:
    """Access-scope check used before asking for human confirmation."""
    return repository.get_project(project_id) is not None
