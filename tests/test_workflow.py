import sqlite3
import unittest
from pathlib import Path

from pydantic import ValidationError

from app import repository
from app.config import settings
from app.database import initialize_database
from app.schemas import ProjectCreate, StoryboardCreate, StoryboardUpdate
from app.services.storyboard import StoryboardGenerationError, _extract_json, _validate, demo_storyboards


STORY = "雨夜里，林夏收到一封没有署名的信。" * 12


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.original_path = settings.database_path
        self.test_path = Path(__file__).resolve().parent / "test_workflow.db"
        self.test_path.unlink(missing_ok=True)
        object.__setattr__(settings, "database_path", self.test_path)
        initialize_database()

    def tearDown(self):
        object.__setattr__(settings, "database_path", self.original_path)
        self.test_path.unlink(missing_ok=True)

    def test_story_requires_at_least_100_characters(self):
        with self.assertRaises(ValidationError):
            ProjectCreate(title="太短", story_text="不足一百字")

    def test_project_storyboard_edit_and_confirm_flow(self):
        project = repository.create_project(ProjectCreate(title="雨夜来信", story_text=STORY))
        items = demo_storyboards(project["story_text"], project["art_style"], 30)
        generated = repository.replace_storyboards(project["id"], items)
        self.assertGreaterEqual(generated["storyboard_count"], 3)
        first = generated["storyboards"][0]
        changed = repository.update_storyboard(first["id"], StoryboardUpdate(dialogue="修改后的台词"))
        self.assertEqual(changed["dialogue"], "修改后的台词")
        confirmed = repository.confirm_storyboards(project["id"])
        self.assertEqual(confirmed["status"], "storyboard_confirmed")
        self.assertTrue(all(row["status"] == "confirmed" for row in confirmed["storyboards"]))

    def test_delete_project_cascades_storyboards(self):
        project = repository.create_project(ProjectCreate(title="待删除", story_text=STORY))
        repository.replace_storyboards(project["id"], demo_storyboards(STORY, "漫画", 30))
        self.assertTrue(repository.delete_project(project["id"]))
        self.assertIsNone(repository.get_project(project["id"]))

    def test_empty_database_and_unknown_project(self):
        self.assertEqual(repository.list_projects(), [])
        self.assertIsNone(repository.get_project("不存在的作品编号"))

    def test_blank_title_is_rejected(self):
        with self.assertRaises(ValidationError):
            ProjectCreate(title="   ", story_text=STORY)

    def test_duplicate_sequence_is_rejected_by_unique_constraint(self):
        project = repository.create_project(ProjectCreate(title="序号冲突", story_text=STORY))
        rows = [
            StoryboardCreate(sequence=1, scene_description="镜头一", dialogue="", emotion="平静", duration=3, image_prompt="画面一"),
            StoryboardCreate(sequence=1, scene_description="镜头二", dialogue="", emotion="平静", duration=3, image_prompt="画面二"),
            StoryboardCreate(sequence=2, scene_description="镜头三", dialogue="", emotion="平静", duration=3, image_prompt="画面三"),
        ]
        with self.assertRaises(sqlite3.IntegrityError):
            repository.replace_storyboards(project["id"], rows)


class StoryboardParsingTests(unittest.TestCase):
    def test_extracts_wrapped_json(self):
        rows = _extract_json('```json\n{"storyboards":[{"sequence":1}]}\n```')
        self.assertEqual(rows[0]["sequence"], 1)

    def test_rejects_non_continuous_sequence(self):
        rows = [
            {"sequence":1,"scene_description":"镜头一","dialogue":"","emotion":"平静","duration":3,"image_prompt":"画面一"},
            {"sequence":3,"scene_description":"镜头二","dialogue":"","emotion":"平静","duration":3,"image_prompt":"画面二"},
            {"sequence":4,"scene_description":"镜头三","dialogue":"","emotion":"平静","duration":3,"image_prompt":"画面三"},
        ]
        with self.assertRaises(StoryboardGenerationError):
            _validate(rows)

    def test_demo_handles_unpunctuated_long_paragraph(self):
        long_story = "甲" * 2900 + "。" + "乙" * 20 + "。" + "丙" * 20
        items = demo_storyboards(long_story, "日系赛璐璐", 120)
        self.assertGreaterEqual(len(items), 3)
        self.assertEqual([item.sequence for item in items], list(range(1, len(items) + 1)))
        self.assertTrue(all(len(item.scene_description) <= 2000 for item in items))


if __name__ == "__main__":
    unittest.main()
