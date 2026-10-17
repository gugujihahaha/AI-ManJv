import unittest

from app.rag import (build_user_prompt, check_citations, classify_intent, rank_chunks,
                     retrieve)
from app.rag_rebuild import MAX_CHUNK_CHARS, build_chunks


class KnowledgeBaseTests(unittest.TestCase):
    def test_chunks_have_metadata_and_respect_limit(self):
        chunks = build_chunks()
        self.assertGreaterEqual(len(chunks), 10)
        for chunk in chunks:
            self.assertTrue(chunk["id"])
            self.assertTrue(chunk["source"].endswith(".md"))
            self.assertTrue(chunk["title"])
            self.assertTrue(chunk["origin"])
            self.assertLessEqual(chunk["chars"], MAX_CHUNK_CHARS)
            self.assertEqual(chunk["chars"], len(chunk["text"]))

    def test_confirmation_question_ranks_confirmation_section_first(self):
        top = retrieve("分镜至少要几个才能确认？")
        self.assertIsNotNone(top)
        self.assertIn(top[0]["title"], {"确认与锁定", "为什么确认按钮不可用"})

    def test_unrelated_question_returns_no_chunks(self):
        self.assertIsNone(retrieve("帮我推荐三家漫画出版社"))

    def test_vague_question_needs_clarification(self):
        question = "这个软件好吗"
        self.assertEqual(classify_intent(question, rank_chunks(question)), "clarify")

    def test_missing_information_question_still_retrieves_failure_guide(self):
        top = retrieve("生成分镜的时候报错了")
        self.assertIsNotNone(top)
        titles = {chunk["title"] for chunk in top}
        self.assertTrue(titles & {"AI 模式的重试与失败", "AI 模式很慢或失败怎么办"})


class CitationTests(unittest.TestCase):
    def test_valid_citations_are_accepted(self):
        result = check_citations("结论一 [来源1]，结论二 [来源2][来源1]。", 2)
        self.assertEqual(result["cited"], [1, 2])
        self.assertEqual(result["invalid"], [])
        self.assertTrue(result["ok"])

    def test_out_of_range_citation_is_rejected(self):
        result = check_citations("结论 [来源3]", 2)
        self.assertEqual(result["invalid"], [3])
        self.assertFalse(result["ok"])

    def test_prompt_contains_source_and_question(self):
        chunks = [{"source": "a.md", "title": "启动步骤", "text": "运行 uvicorn。"}]
        prompt = build_user_prompt("怎么启动？", chunks)
        self.assertIn("[来源1]", prompt)
        self.assertIn("a.md", prompt)
        self.assertIn("怎么启动？", prompt)


if __name__ == "__main__":
    unittest.main()
