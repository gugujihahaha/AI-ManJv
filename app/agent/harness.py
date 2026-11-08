"""Runtime harness: model loop, tool dispatch, confirmation, limits and logs."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app import repository
from app.agent.model import ModelClient, ModelError
from app.agent.prompts import SYSTEM_PROMPT, build_correction_message, build_task_message
from app.agent.tools import (TOOL_SPECS, ToolSpec, ToolValidationError, execute_tool,
                             get_tool_definitions, parse_tool_arguments,
                             project_scope_ok, validate_arguments)
from app.config import settings
from app.database import initialize_database


@dataclass
class RunConfig:
    max_steps: int = 8                 # one step = one model response
    max_tool_calls: int = 12
    max_calls_per_tool: int = 4
    max_corrections: int = 1
    model_timeout: float = 40.0
    tool_timeout: float = 10.0
    total_timeout: float = 90.0
    confirm_timeout: float = 60.0
    temperature: float = 0.2
    max_tokens: int = 1200


def _new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _extract_json_object(text: str) -> dict | None:
    cleaned = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.IGNORECASE)
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        candidates.append(cleaned[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _preview(value: Any, limit: int = 600) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _summarize_args(args: dict) -> dict:
    summary: dict = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > 80:
            summary[key] = value[:80] + "…"
        else:
            summary[key] = value
    return summary


def _safe_result_preview(name: str, result: dict, limit: int = 600) -> str:
    """Keep run logs useful without copying story text into them."""
    if result.get("status") != "ok":
        return _preview(result, limit)
    data = result.get("data")
    if name == "get_project_detail" and isinstance(data, dict):
        return _preview({
            "status": "ok",
            "data": {
                "project_id": data.get("project_id"),
                "title": data.get("title"),
                "storyboard_count": data.get("storyboard_count"),
                "truncated": data.get("truncated"),
            },
        }, limit)
    if name == "list_projects" and isinstance(data, dict):
        return _preview({
            "status": "ok",
            "data": {
                "count": data.get("count"),
                "projects": [{"id": row.get("id"), "title": row.get("title")}
                             for row in data.get("projects", [])[:5]],
            },
        }, limit)
    if name == "save_analysis_note" and isinstance(data, dict):
        return _preview({
            "status": "ok",
            "data": {
                "note_id": data.get("note_id"),
                "project_id": data.get("project_id"),
                "run_id": data.get("run_id"),
                "summary_chars": data.get("summary_chars"),
                "summary_preview": data.get("summary_preview"),
            },
        }, limit)
    return _preview(result, limit)


def check_success(content: str, state: dict, require_note: bool = False) -> dict:
    """Program-side success check. The model's claim is not enough."""
    problems: list[str] = []
    parsed = _extract_json_object(content)
    stats = state.get("stats_result")
    if stats is None:
        problems.append("没有调用 compute_storyboard_stats 获取统计结果")
    if parsed is None:
        problems.append("最终回答不是合法 JSON 对象")
        return {"ok": False, "problems": problems, "parsed": None}

    if stats is not None:
        if parsed.get("project_title") != stats.get("title"):
            problems.append("project_title 与工具返回的作品名称不一致")
        if parsed.get("shot_count") != stats.get("shot_count"):
            problems.append(f"shot_count 与工具结果不一致，工具结果为 {stats.get('shot_count')}")
        for field in ("total_duration", "average_duration"):
            value = parsed.get(field)
            expected = stats.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                problems.append(f"{field} 必须是数字")
            elif abs(float(value) - float(expected)) > 0.1:
                problems.append(f"{field} 与工具结果不一致，工具结果为 {expected}")

        issues = parsed.get("issues")
        if not isinstance(issues, list):
            problems.append("issues 必须是数组")
        else:
            expected_min = min(2, len(stats.get("issues", [])))
            if len(issues) < expected_min:
                problems.append(f"issues 至少需要 {expected_min} 条")

    conclusion = parsed.get("conclusion")
    conclusion_text = conclusion if isinstance(conclusion, str) else ""
    if len(conclusion_text.strip()) < 10:
        problems.append("conclusion 必须是不少于 10 字的说明")

    if require_note:
        note_status = state.get("note_status", "none")
        if note_status == "approved":
            if "未保存" in conclusion_text or "拒绝" in conclusion_text:
                problems.append("备注已经保存，conclusion 不应声明未保存")
            elif "保存" not in conclusion_text:
                problems.append("conclusion 需要说明备注已经保存")
        elif note_status == "denied":
            if not any(word in conclusion_text for word in ("未保存", "拒绝")):
                problems.append("用户拒绝了备注保存，conclusion 需要说明未保存")
        else:
            problems.append("任务要求保存备注，但没有完成 save_analysis_note 调用")

    return {"ok": not problems, "problems": problems, "parsed": parsed}


class AgentHarness:
    def __init__(self, model_client: ModelClient | None = None,
                 registry: dict[str, ToolSpec] | None = None,
                 config: RunConfig | None = None,
                 confirm_fn: Callable[[dict], Any] | None = None,
                 runs_dir: Path | None = None,
                 log_path: Path | None = None) -> None:
        self.model_client = model_client or ModelClient()
        self.registry = registry or TOOL_SPECS
        self.config = config or RunConfig()
        self.confirm_fn = confirm_fn or (lambda _request: False)
        self.runs_dir = Path(runs_dir or settings.agent_runs_dir)
        self.log_path = Path(log_path or settings.agent_log_path)

    def run(self, task: str, project_id: str, require_note: bool = False) -> dict:
        initialize_database()
        config = self.config
        run_id = _new_run_id()
        started_at = _now()
        started = time.monotonic()
        deadline = started + config.total_timeout
        state: dict = {
            "run_id": run_id,
            "task": task,
            "project_id": project_id,
            "require_note": require_note,
            "started_at": started_at,
            "ended_at": "",
            "status": "running",
            "error": "",
            "steps": 0,
            "model_calls": 0,
            "tool_calls": 0,
            "per_tool_counts": {},
            "corrections": 0,
            "tool_call_records": [],
            "events": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "models": [],
            "stats_result": None,
            "note_status": "none",
            "success_check": None,
            "final_answer": "",
            "elapsed_ms": 0,
            "limits": asdict(config),
            "context_chars": 0,
            "message_count": 0,
        }
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_task_message(project_id, task, require_note)},
        ]
        tool_defs = get_tool_definitions(self.registry)
        stop_reason = ""

        while state["steps"] < config.max_steps:
            if time.monotonic() > deadline:
                stop_reason = "TOTAL_TIMEOUT"
                break
            state["steps"] += 1
            self._event(state, "model_request", step=state["steps"],
                        message_count=len(messages))
            try:
                response = self.model_client.call(
                    messages, tools=tool_defs,
                    temperature=config.temperature, max_tokens=config.max_tokens,
                    timeout=config.model_timeout,
                )
            except ModelError as exc:
                state["status"] = "failed"
                state["error"] = f"{exc.error_type}: {exc}"
                self._event(state, "model_error", error_type=exc.error_type, message=str(exc))
                break

            state["model_calls"] += 1
            model_name = response.get("model", "")
            if model_name:
                state["models"].append(model_name)
            usage = response.get("usage") or {}
            for key in state["usage"]:
                state["usage"][key] += int(usage.get(key, 0) or 0)

            if time.monotonic() > deadline:
                stop_reason = "TOTAL_TIMEOUT"
                break

            message = response.get("message") or {}
            messages.append(self._sanitize_assistant(message))
            tool_calls = message.get("tool_calls") or []
            self._event(state, "model_response", step=state["steps"],
                        tool_call_count=len(tool_calls),
                        content_chars=len(message.get("content") or ""),
                        model=model_name,
                        elapsed_ms=response.get("elapsed_ms", 0))

            if tool_calls:
                for call in tool_calls:
                    if state["tool_calls"] >= config.max_tool_calls:
                        stop_reason = "MAX_TOOL_CALLS"
                        break
                    state["tool_calls"] += 1
                    record, result = self._handle_tool_call(call, state)
                    state["tool_call_records"].append(record)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": record["call_id"],
                        "name": record["name"],
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                if stop_reason:
                    break
                continue

            content = message.get("content") or ""
            check = check_success(content, state, require_note)
            state["success_check"] = check
            if check["ok"]:
                state["status"] = "success"
                state["final_answer"] = content
                self._event(state, "success", step=state["steps"])
                break
            if state["corrections"] >= config.max_corrections:
                state["status"] = "failed"
                state["error"] = "SUCCESS_CHECK_FAILED"
                state["final_answer"] = content
                self._event(state, "success_check_failed", problems=check["problems"])
                break
            state["corrections"] += 1
            self._event(state, "success_check_failed", problems=check["problems"])
            messages.append({
                "role": "user",
                "content": build_correction_message(check["problems"]),
            })
        else:
            stop_reason = "MAX_STEPS"

        if state["status"] == "running":
            state["status"] = "stopped"
            state["error"] = stop_reason or "STOPPED"
            self._event(state, "stop", reason=state["error"])
        return self._finalize(state, started, messages)

    def _handle_tool_call(self, call: dict, state: dict) -> tuple[dict, dict]:
        function = call.get("function") or {}
        name = function.get("name", "")
        raw_args = function.get("arguments", "{}")
        record: dict = {
            "call_id": call.get("id", ""),
            "step": state["steps"],
            "name": name,
            "arguments_raw": raw_args if isinstance(raw_args, str) else _preview(raw_args, 200),
            "arguments": None,
            "status": "unknown",
            "error_type": "",
            "message": "",
            "confirmation": None,
            "elapsed_ms": 0,
            "result_preview": "",
            "result_chars": 0,
        }
        started = time.perf_counter()
        try:
            args = parse_tool_arguments(raw_args)
            record["arguments"] = _summarize_args(args)
            spec = self.registry.get(name)
            if spec is None:
                raise ToolValidationError(f"工具 {name} 不在允许列表中")
            validate_arguments(spec, args)
        except ToolValidationError as exc:
            record["status"] = "error"
            record["error_type"] = "VALIDATION_ERROR"
            record["message"] = str(exc)
            record["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
            result = {"status": "error", "error_type": "VALIDATION_ERROR", "message": str(exc)}
            self._event(state, "tool_call", **self._record_brief(record))
            return record, result

        count = state["per_tool_counts"].get(name, 0)
        if count >= self.config.max_calls_per_tool:
            message = f"工具 {name} 本次运行最多调用 {self.config.max_calls_per_tool} 次"
            record.update(status="error", error_type="TOOL_CALL_LIMIT", message=message,
                          elapsed_ms=int((time.perf_counter() - started) * 1000))
            self._event(state, "tool_call", **self._record_brief(record))
            return record, {"status": "error", "error_type": "TOOL_CALL_LIMIT", "message": message}
        state["per_tool_counts"][name] = count + 1

        project_id = args.get("project_id")
        if project_id is not None and not project_scope_ok(project_id):
            message = f"作品不存在或无权访问：{project_id}"
            record.update(status="error", error_type="ACCESS_DENIED", message=message,
                          elapsed_ms=int((time.perf_counter() - started) * 1000))
            self._event(state, "tool_call", **self._record_brief(record))
            return record, {"status": "error", "error_type": "ACCESS_DENIED", "message": message}

        if spec.requires_confirmation:
            project = repository.get_project(project_id) if project_id else None
            request = {
                "tool": name,
                "arguments": args,
                "project_title": project["title"] if project else "",
                "impact": spec.impact,
                "summary_preview": str(args.get("summary", ""))[:120],
            }
            decision = self._ask_confirmation(request)
            record["confirmation"] = decision
            if not decision["approved"]:
                record["status"] = "denied" if decision["decision"] != "timeout" else "timeout"
                record["error_type"] = ("CONFIRM_DENIED" if decision["decision"] != "timeout"
                                        else "CONFIRM_TIMEOUT")
                record["message"] = decision["message"]
                record["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
                if name == "save_analysis_note":
                    state["note_status"] = "denied"
                result = {"status": record["status"], "error_type": record["error_type"],
                          "message": record["message"]}
                self._event(state, "tool_call", **self._record_brief(record))
                return record, result

        result = execute_tool(name, args, {"run_id": state["run_id"]},
                              registry=self.registry,
                              timeout=min(self.config.tool_timeout, spec.timeout_seconds))
        record["status"] = result.get("status", "unknown")
        record["error_type"] = result.get("error_type", "")
        record["message"] = result.get("message", "")
        record["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        record["result_preview"] = _safe_result_preview(name, result)
        record["result_chars"] = len(json.dumps(result, ensure_ascii=False, default=str))

        if name == "compute_storyboard_stats" and result.get("status") == "ok":
            state["stats_result"] = result.get("data")
        if name == "save_analysis_note":
            if result.get("status") == "ok":
                state["note_status"] = "approved"
            elif result.get("status") == "timeout":
                state["note_status"] = "unknown"
            elif result.get("status") != "denied":
                state["note_status"] = "failed"

        self._event(state, "tool_call", **self._record_brief(record))
        return record, result

    def _ask_confirmation(self, request: dict) -> dict:
        box: dict = {}
        started = time.perf_counter()

        def worker() -> None:
            try:
                box["value"] = self.confirm_fn(request)
            except Exception as exc:  # noqa: BLE001 - a broken callback means no consent
                box["error"] = f"{type(exc).__name__}: {exc}"

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(self.config.confirm_timeout)
        elapsed = int((time.perf_counter() - started) * 1000)
        if thread.is_alive():
            return {"approved": False, "decision": "timeout", "elapsed_ms": elapsed,
                    "message": f"等待用户确认超过 {self.config.confirm_timeout:.0f} 秒，按拒绝处理"}
        if "error" in box:
            return {"approved": False, "decision": "error", "elapsed_ms": elapsed,
                    "message": f"确认回调异常：{box['error']}"}
        value = box.get("value")
        if isinstance(value, str):
            lowered = value.strip().lower()
            approved = lowered in {"approve", "approved", "y", "yes", "同意"}
            decision = "approved" if approved else "denied"
            if lowered == "timeout":
                decision = "timeout"
                approved = False
        else:
            approved = bool(value)
            decision = "approved" if approved else "denied"
        return {"approved": approved, "decision": decision, "elapsed_ms": elapsed,
                "message": "用户已同意" if approved else "用户拒绝执行"}

    @staticmethod
    def _record_brief(record: dict) -> dict:
        return {
            "call_id": record["call_id"],
            "step": record["step"],
            "name": record["name"],
            "arguments": record["arguments"],
            "status": record["status"],
            "error_type": record["error_type"],
            "message": record["message"],
            "confirmation": record["confirmation"],
            "elapsed_ms": record["elapsed_ms"],
            "result_preview": record["result_preview"][:300],
        }

    @staticmethod
    def _sanitize_assistant(message: dict) -> dict:
        result: dict = {"role": "assistant", "content": message.get("content") or ""}
        calls = message.get("tool_calls") or []
        if calls:
            result["tool_calls"] = [{
                "id": call.get("id", ""),
                "type": "function",
                "function": {
                    "name": (call.get("function") or {}).get("name", ""),
                    "arguments": (call.get("function") or {}).get("arguments", "{}"),
                },
            } for call in calls]
        return result

    @staticmethod
    def _event(state: dict, event: str, **fields: Any) -> None:
        item = {"event": event, "at": _now(), **fields}
        state["events"].append(item)

    def _finalize(self, state: dict, started: float, messages: list[dict]) -> dict:
        state["ended_at"] = _now()
        state["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        state["context_chars"] = sum(len(str(item.get("content") or "")) for item in messages)
        state["message_count"] = len(messages)
        if state["status"] == "running":
            state["status"] = "failed"
            state["error"] = state["error"] or "UNKNOWN"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        target = self.runs_dir / f"{state['run_id']}.json"
        target.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str),
                          encoding="utf-8")
        state["log_path"] = str(target)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        line = (f"{state['started_at']} {state['run_id']} status={state['status']} "
                f"steps={state['steps']} tools={state['tool_calls']} "
                f"elapsed_ms={state['elapsed_ms']} error={state['error']}\n")
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        return state
