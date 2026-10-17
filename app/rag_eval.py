"""Batch evaluation for the ManJv usage assistant.

The script writes both JSON (full answers) and Markdown (readable summary)
under docs/rag/samples so the report can cite one file per test group.

Modes:
    python -m app.rag_eval --mode groups
    python -m app.rag_eval --mode compare
    python -m app.rag_eval --mode prompts
    python -m app.rag_eval --mode context
    python -m app.rag_eval --mode all
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.rag import (SYSTEM_PROMPT_RAG, SYSTEM_PROMPT_V1, ask, ask_direct,
                     build_user_prompt, call_model, check_citations, load_chunks)


SAMPLES_DIR = settings.knowledge_chunks_path.parent / "samples"

QUESTIONS = [
    {"id": "Q1", "question": "软件如何启动？端口是多少？", "type": "单片段",
     "rule": "提到 uvicorn 启动命令与 8000 端口"},
    {"id": "Q2", "question": "分镜至少要几个才能确认？", "type": "单片段",
     "rule": "至少 3 个，且确认后不可再编辑"},
    {"id": "Q3", "question": "demo 模式和 AI 模式有什么区别？", "type": "多片段",
     "rule": "demo 离线确定性切分、AI 调用大模型、失败可降级"},
    {"id": "Q4", "question": "作品和分镜数据存在哪里？怎么清空？", "type": "多片段",
     "rule": "storage 下的 SQLite 文件，删除后重启自动重建"},
    {"id": "Q5", "question": "AI 模式生成分镜失败怎么办？", "type": "单片段",
     "rule": "自动重试一次，仍失败转 502，可用 demo 模式降级"},
    {"id": "Q6", "question": "帮我推荐三家漫画出版社", "type": "资料无答案",
     "rule": "提示资料不足或超出范围，不编造"},
    {"id": "Q7", "question": "这个软件好吗", "type": "含义不清",
     "rule": "追问澄清具体想了解的功能"},
]

GROUPS = [
    {"id": "T1", "question": "分镜至少要几个才能确认？", "type": "正常问题",
     "expected": "回答至少 3 个，并说明确认后冻结"},
    {"id": "T2", "question": "生成分镜的时候报错了", "type": "缺少必要信息",
     "expected": "追问错误提示、模式或作品状态，再给排查方向"},
    {"id": "T3", "question": "帮我生成一张漫画图片", "type": "超出任务范围",
     "expected": "说明只解答软件使用问题，当前版本没有出图功能"},
]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _save(name: str, payload: dict) -> tuple[Path, Path]:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    json_path = SAMPLES_DIR / f"{name}.json"
    md_path = SAMPLES_DIR / f"{name}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(payload["markdown"], encoding="utf-8")
    return json_path, md_path


def _result_block(title: str, result: dict) -> list[str]:
    lines = [f"### {title}", "", f"处理结果：{result.get('intent', '')}", ""]
    if result.get("sources"):
        lines.append("检索片段：")
        for item in result["sources"]:
            lines.append(f"- [来源{item['index']}] {item['source']} / {item['title']}（得分 {item['score']}）")
        lines.append("")
    lines.extend(["回答：", "", result.get("answer", ""), ""])
    citations = result.get("citations") or {}
    if citations.get("cited") is not None:
        lines.append(f"引用核对：{citations}")
    usage = result.get("usage") or {}
    lines.append(f"耗时 {result.get('elapsed_ms', 0)} ms；Token {usage.get('total_tokens', 0)}"
                 f"（输入 {usage.get('prompt_tokens', 0)} / 输出 {usage.get('completion_tokens', 0)}）；"
                 f"上下文 {result.get('context_chars', 0)} 字符；返回模型 {result.get('model', '')}")
    lines.append("")
    return lines


def ask_forced(question: str, prompt: str, chunks: list[dict]) -> dict:
    """Bypass the intent gate so a prompt version can be compared directly."""
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": build_user_prompt(question, chunks)
         if chunks else f"资料：\n\n用户问题：{question}"},
    ]
    answer, meta = call_model(messages)
    return {
        "question": question,
        "answer": answer,
        "intent": "answered",
        "sources": [
            {"index": index, "source": chunk["source"], "title": chunk["title"],
             "score": chunk.get("score", 0)}
            for index, chunk in enumerate(chunks, 1)
        ],
        "chunks": chunks,
        "citations": check_citations(answer, len(chunks)),
        "messages": messages,
        "context_chars": sum(len(message["content"]) for message in messages),
        **meta,
    }


def run_groups() -> dict:
    items = []
    for group in GROUPS:
        started = time.perf_counter()
        result = ask(group["question"])
        record = {**group, "wall_ms": int((time.perf_counter() - started) * 1000), "result": result}
        items.append(record)
        print(f"{group['id']} {group['type']} -> {result['intent']}，"
              f"{result['usage']['total_tokens']} tokens")
    payload = {"name": "三组输入测试", "run_at": _now(), "items": items}
    lines = [f"# {payload['name']}", "", f"运行时间：{payload['run_at']}", ""]
    for item in items:
        lines.extend([f"## {item['id']} {item['type']}", "", f"问题：{item['question']}",
                      f"期望：{item['expected']}", ""])
        lines.extend(_result_block("实际返回", item["result"]))
    payload["markdown"] = "\n".join(lines)
    return payload


def run_compare() -> dict:
    items = []
    for question in QUESTIONS:
        direct = ask_direct(question["question"])
        rag = ask(question["question"])
        record = {**question, "direct": direct, "rag": rag}
        items.append(record)
        print(f"{question['id']} 直接 {direct['usage']['total_tokens']} tokens / "
              f"RAG {rag['usage']['total_tokens']} tokens，RAG 意图 {rag['intent']}")
    payload = {"name": "直接模型与 RAG 对比", "run_at": _now(), "items": items}
    lines = [f"# {payload['name']}", "", f"运行时间：{payload['run_at']}", "",
             "两组使用相同模型与 temperature，直接组不提供资料，RAG 组提供 Top-k 片段并要求引用。", ""]
    for item in items:
        lines.extend([f"## {item['id']} {item['type']}", "", f"问题：{item['question']}",
                      f"判断规则：{item['rule']}", ""])
        lines.extend(_result_block("直接模型组", item["direct"]))
        lines.extend(_result_block("RAG 组", item["rag"]))
    payload["markdown"] = "\n".join(lines)
    return payload


def run_prompts() -> dict:
    q2 = QUESTIONS[1]["question"]
    q6 = QUESTIONS[5]["question"]
    q7 = QUESTIONS[6]["question"]
    items = [
        {"id": "P1", "question": q2, "version": "V1",
         "note": "仅要求根据资料回答，不要求引用", "result": ask(q2, prompt=SYSTEM_PROMPT_V1)},
        {"id": "P2", "question": q2, "version": "V2",
         "note": "增加引用、拒答、追问规则", "result": ask(q2)},
        {"id": "P3", "question": q7, "version": "V1（强制调用，空资料）",
         "note": "V1 没有拒答与追问规则，绕过检索闸门直接看模型行为",
         "result": ask_forced(q7, SYSTEM_PROMPT_V1, [])},
        {"id": "P4", "question": q7, "version": "V2（完整流程）",
         "note": "检索无相关片段，先由检索闸门判定需要澄清", "result": ask(q7)},
        {"id": "P5", "question": q6, "version": "V1（强制调用，空资料）",
         "note": "V1 没有拒答规则，观察是否会凭通用知识作答",
         "result": ask_forced(q6, SYSTEM_PROMPT_V1, [])},
        {"id": "P6", "question": q6, "version": "V2（完整流程）",
         "note": "检索无相关片段，直接返回资料不足", "result": ask(q6)},
    ]
    for item in items:
        print(f"{item['id']} {item['version']} -> {item['result']['usage']['total_tokens']} tokens")
    payload = {"name": "Prompt 版本对照", "run_at": _now(), "items": items}
    lines = [f"# {payload['name']}", "", f"运行时间：{payload['run_at']}", ""]
    for item in items:
        lines.extend([f"## {item['id']} {item['version']}", "", f"问题：{item['question']}",
                      f"说明：{item['note']}", ""])
        lines.extend(_result_block("返回", item["result"]))
    payload["markdown"] = "\n".join(lines)
    return payload


def run_context() -> dict:
    question = QUESTIONS[1]["question"]
    all_chunks = load_chunks()
    all_result = ask_forced(question, SYSTEM_PROMPT_RAG, all_chunks)
    top_result = ask(question)
    items = [
        {"id": "C1", "setting": "全部片段", "chunk_count": len(all_chunks), "result": all_result},
        {"id": "C2", "setting": "Top-3", "chunk_count": len(top_result["chunks"]), "result": top_result},
    ]
    print(f"全部片段 {len(all_chunks)} 个 -> {all_result['usage']['prompt_tokens']} 输入 tokens；"
          f"Top-3 -> {top_result['usage']['prompt_tokens']} 输入 tokens")
    payload = {"name": "上下文精简对照", "run_at": _now(), "question": question, "items": items}
    lines = [f"# {payload['name']}", "", f"运行时间：{payload['run_at']}", "", f"问题：{question}", ""]
    for item in items:
        lines.extend([f"## {item['id']} {item['setting']}", "",
                      f"片段数：{item['chunk_count']}；上下文字符数：{item['result']['context_chars']}", ""])
        lines.extend(_result_block("回答", item["result"]))
    payload["markdown"] = "\n".join(lines)
    return payload


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="漫剧工厂 RAG 批量测试")
    parser.add_argument("--mode", choices=("groups", "compare", "prompts", "context", "all"),
                        default="all")
    args = parser.parse_args()

    modes = ("groups", "compare", "prompts", "context") if args.mode == "all" else (args.mode,)
    runners = {
        "groups": run_groups,
        "compare": run_compare,
        "prompts": run_prompts,
        "context": run_context,
    }
    for mode in modes:
        print(f"\n=== {mode} ===")
        payload = runners[mode]()
        json_path, md_path = _save(mode, payload)
        print(f"已保存：{json_path}")
        print(f"已保存：{md_path}")


if __name__ == "__main__":
    main()
