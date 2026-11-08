import json
import time
import unittest
from pathlib import Path

from app import repository
from app.agent.harness import AgentHarness, RunConfig, check_success
from app.agent.testing import (ScriptedModel, assistant_final, assistant_tool_call,
                               final_json, make_test_registry)
from app.agent.tools import execute_tool
from app.config import settings
from app.database import initialize_database
from app.schemas import ProjectCreate
from app.services.storyboard import demo_storyboards


STORY = "雨夜里，林夏收到一封没有署名的信。" * 12
NOTE = "这是一段用于测试的分析备注，长度超过十个字，内容不涉及个人隐私。"


class AgentTestBase(unittest.TestCase):
    def setUp(self):
        self.original_path = settings.database_path
        self.test_dir = Path(__file__).resolve().parent
        self.test_path = self.test_dir / "test_agent.db"
        self.test_path.unlink(missing_ok=True)
        object.__setattr__(settings, "database_path", self.test_path)
        initialize_database()
        project = repository.create_project(ProjectCreate(title="雨夜来信", story_text=STORY))
        self.project_id = project["id"]
        repository.replace_storyboards(self.project_id, demo_storyboards(STORY, "日系赛璐璐", 120))
        self.stats = execute_tool("compute_storyboard_stats",
                                  {"project_id": self.project_id},
                                  {"run_id": "test-setup"})["data"]

    def tearDown(self):
        object.__setattr__(settings, "database_path", self.original_path)
        self.test_path.unlink(missing_ok=True)

    def harness(self, model, **config) -> AgentHarness:
        registry = config.pop("registry", None)
        confirm_fn = config.pop("confirm_fn", lambda _request: False)
        return AgentHarness(
            model_client=model,
            registry=registry,
            config=RunConfig(**config),
            confirm_fn=confirm_fn,
            runs_dir=self.test_dir / "_agent_runs",
            log_path=self.test_dir / "_agent_runs" / "agent.log",
        )


class ToolValidationTests(AgentTestBase):
    def test_wrong_argument_type_is_rejected(self):
        result = execute_tool("get_project_detail", {"project_id": 123}, {"run_id": "t"})
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "VALIDATION_ERROR")

    def test_unknown_tool_is_rejected(self):
        result = execute_tool("delete_project", {"project_id": self.project_id}, {"run_id": "t"})
        self.assertEqual(result["error_type"], "UNKNOWN_TOOL")

    def test_missing_required_field_is_rejected(self):
        result = execute_tool("compute_storyboard_stats", {}, {"run_id": "t"})
        self.assertEqual(result["error_type"], "VALIDATION_ERROR")

    def test_unknown_project_is_denied(self):
        result = execute_tool("get_project_detail", {"project_id": "not-exist"}, {"run_id": "t"})
        self.assertEqual(result["error_type"], "ACCESS_DENIED")

    def test_stats_reports_expected_values_and_issues(self):
        self.assertEqual(self.stats["shot_count"], 12)
        self.assertEqual(self.stats["total_duration"], 120.0)
        self.assertEqual(self.stats["average_duration"], 10.0)
        codes = {item["code"] for item in self.stats["issues"]}
        self.assertIn("UNIFORM_DURATION", codes)
        self.assertIn("EMOTION_UNCONFIRMED", codes)
        self.assertGreaterEqual(self.stats["issue_count"], 2)


class NoteAndSuccessTests(AgentTestBase):
    def test_note_is_idempotent_per_run(self):
        repository.save_analysis_note(self.project_id, "run-1", NOTE)
        repository.save_analysis_note(self.project_id, "run-1", NOTE + "补充")
        self.assertEqual(repository.count_analysis_notes(self.project_id), 1)
        repository.save_analysis_note(self.project_id, "run-2", NOTE)
        self.assertEqual(repository.count_analysis_notes(self.project_id), 2)

    def test_success_check_rejects_wrong_number(self):
        state = {"stats_result": self.stats, "note_status": "none"}
        bad = json.dumps({
            "project_title": self.stats["title"],
            "shot_count": 12,
            "total_duration": 999,
            "average_duration": 10,
            "issues": ["问题一", "问题二"],
            "conclusion": "这段结论长度足够但数字错误。",
        }, ensure_ascii=False)
        self.assertFalse(check_success(bad, state)["ok"])
        good = final_json(self.stats)
        self.assertTrue(check_success(good, state)["ok"])


class HarnessLoopTests(AgentTestBase):
    def test_parameter_error_is_fed_back_and_corrected(self):
        model = ScriptedModel([
            assistant_tool_call("get_project_detail", {"project_id": 123}, "bad"),
            assistant_tool_call("get_project_detail", {"project_id": self.project_id}, "good"),
            assistant_tool_call("compute_storyboard_stats", {"project_id": self.project_id}, "stats"),
            assistant_final(final_json(self.stats)),
        ])
        result = self.harness(model, max_steps=6).run("分析分镜。", self.project_id)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["tool_call_records"][0]["error_type"], "VALIDATION_ERROR")

    def test_tool_exception_is_recorded_and_recovered(self):
        model = ScriptedModel([
            assistant_tool_call("__test_boom", {}, "boom"),
            assistant_tool_call("compute_storyboard_stats", {"project_id": self.project_id}, "stats"),
            assistant_final(final_json(self.stats)),
        ])
        result = self.harness(model, max_steps=6, registry=make_test_registry()).run(
            "分析分镜。", self.project_id)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["tool_call_records"][0]["error_type"], "TOOL_EXCEPTION")

    def test_tool_timeout_is_recorded_and_recovered(self):
        model = ScriptedModel([
            assistant_tool_call("__test_slow", {}, "slow"),
            assistant_tool_call("compute_storyboard_stats", {"project_id": self.project_id}, "stats"),
            assistant_final(final_json(self.stats)),
        ])
        result = self.harness(model, max_steps=6, tool_timeout=0.2,
                              registry=make_test_registry()).run("分析分镜。", self.project_id)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["tool_call_records"][0]["error_type"], "TOOL_TIMEOUT")

    def test_max_steps_stops_the_run(self):
        model = ScriptedModel([
            assistant_tool_call("list_projects", {"limit": 3}, "loop-1"),
            assistant_tool_call("list_projects", {"limit": 3}, "loop-2"),
            assistant_tool_call("list_projects", {"limit": 3}, "loop-3"),
        ])
        result = self.harness(model, max_steps=2).run("一直查作品。", self.project_id)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["error"], "MAX_STEPS")
        self.assertEqual(result["steps"], 2)

    def test_write_is_not_saved_when_confirmation_is_denied(self):
        model = ScriptedModel([
            assistant_tool_call("compute_storyboard_stats", {"project_id": self.project_id}, "stats"),
            assistant_tool_call("save_analysis_note",
                                {"project_id": self.project_id, "summary": NOTE}, "note"),
            assistant_final(final_json(self.stats, "用户拒绝，未保存备注；节奏结论：时长分布均匀。")),
        ])
        result = self.harness(model, max_steps=6, confirm_fn=lambda _request: False).run(
            "分析并保存备注。", self.project_id, require_note=True)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["note_status"], "denied")
        self.assertEqual(repository.count_analysis_notes(self.project_id), 0)

    def test_write_is_saved_when_confirmation_is_approved(self):
        model = ScriptedModel([
            assistant_tool_call("compute_storyboard_stats", {"project_id": self.project_id}, "stats"),
            assistant_tool_call("save_analysis_note",
                                {"project_id": self.project_id, "summary": NOTE}, "note"),
            assistant_final(final_json(self.stats, "已保存备注；节奏结论：时长分布均匀。")),
        ])
        result = self.harness(model, max_steps=6, confirm_fn=lambda _request: True).run(
            "分析并保存备注。", self.project_id, require_note=True)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["note_status"], "approved")
        self.assertEqual(repository.count_analysis_notes(self.project_id), 1)

    def test_confirmation_timeout_is_denied(self):
        model = ScriptedModel([
            assistant_tool_call("compute_storyboard_stats", {"project_id": self.project_id}, "stats"),
            assistant_tool_call("save_analysis_note",
                                {"project_id": self.project_id, "summary": NOTE}, "note"),
            assistant_final(final_json(self.stats, "确认超时，未保存备注；节奏结论：时长分布均匀。")),
        ])
        result = self.harness(model, max_steps=6, confirm_timeout=0.1,
                              confirm_fn=lambda _request: time.sleep(0.5)).run(
            "分析并保存备注。", self.project_id, require_note=True)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["note_status"], "denied")
        self.assertEqual(result["tool_call_records"][1]["error_type"], "CONFIRM_TIMEOUT")

    def test_model_error_ends_the_run(self):
        model = ScriptedModel([])
        result = self.harness(model).run("分析分镜。", self.project_id)
        self.assertEqual(result["status"], "failed")
        self.assertIn("MODEL_SCRIPT_EXHAUSTED", result["error"])


if __name__ == "__main__":
    unittest.main()
