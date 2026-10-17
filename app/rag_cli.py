"""Command-line entry for the ManJv usage assistant.

Examples:
    python -m app.rag_cli "分镜至少要几个才能确认？"
    python -m app.rag_cli --show-context "分镜至少要几个才能确认？"
    python -m app.rag_cli --json "demo 模式和 AI 模式有什么区别？"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.rag import RagError, SYSTEM_PROMPT_RAG, SYSTEM_PROMPT_V1, ask, ask_direct


def _print_result(result: dict, show_context: bool) -> None:
    print(f"问题：{result['question']}")
    if result["intent"] != "answered":
        print(f"处理：{'需要澄清' if result['intent'] == 'clarify' else '资料不足'}")
        print(f"回答：{result['answer']}")
        return

    print(f"检索：{len(result['sources'])} 个片段（k={len(result['sources'])}，阈值 2.0）")
    for item in result["sources"]:
        print(f"  {item['index']}. [来源{item['index']}] {item['source']} / {item['title']}"
              f"  得分 {item['score']:.1f}")
    if show_context:
        print("\n----- 实际发送的完整输入 -----")
        for message in result["messages"]:
            print(f"[{message['role']}]")
            print(message["content"])
            print()
        print("----- 输入结束 -----\n")
    print(f"回答：{result['answer']}")
    citations = result["citations"]
    if citations["invalid"]:
        print(f"引用核对：存在越界编号 {citations['invalid']}")
    elif citations["cited"]:
        print("引用核对：有效，引用 " + "、".join(f"来源{number}" for number in citations["cited"]))
    else:
        print("引用核对：回答未标注来源")
    usage = result["usage"]
    print(f"耗时：{result['elapsed_ms']} ms；Token：输入 {usage['prompt_tokens']} / "
          f"输出 {usage['completion_tokens']} / 合计 {usage['total_tokens']}；"
          f"返回模型：{result['model']}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="漫剧工厂使用助手（RAG 命令行）")
    parser.add_argument("question", nargs="+", help="用户问题")
    parser.add_argument("--k", type=int, default=3, help="检索片段数，默认 3")
    parser.add_argument("--direct", action="store_true", help="直接调用模型，不检索资料")
    parser.add_argument("--prompt-version", choices=("v1", "v2"), default="v2",
                        help="v1 为仅要求根据资料回答的旧模板，v2 为带引用与拒答规则的模板")
    parser.add_argument("--show-context", action="store_true", help="打印实际发送给模型的消息")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 结果")
    parser.add_argument("--save", type=Path, help="把完整结果保存为 JSON 文件")
    args = parser.parse_args()

    question = " ".join(args.question)
    try:
        if args.direct:
            result = ask_direct(question)
        else:
            prompt = SYSTEM_PROMPT_V1 if args.prompt_version == "v1" else SYSTEM_PROMPT_RAG
            result = ask(question, k=args.k, prompt=prompt)
    except RagError as exc:
        print(f"调用失败：{exc}")
        raise SystemExit(1) from exc

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_result(result, args.show_context)


if __name__ == "__main__":
    main()
