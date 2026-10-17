"""Rebuild the RAG knowledge chunks from Markdown files.

Run ``python -m app.rag_rebuild`` after editing anything under docs/rag/kb.
The splitter uses level-2 headings and blank-line paragraphs, then cuts a
paragraph longer than MAX_CHUNK_CHARS at sentence punctuation. Adjacent
chunks do not overlap because the knowledge base is small and each section is
already a self-contained topic.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings


MAX_CHUNK_CHARS = 500
CHUNK_OVERLAP = 0


def _split_long_text(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Cut one long paragraph at sentence punctuation, then hard-cut leftovers."""
    if len(text) <= limit:
        return [text]
    pieces = re.split(r"(?<=[。！？；])", text)
    chunks: list[str] = []
    buffer = ""
    for piece in pieces:
        if not piece:
            continue
        if len(buffer) + len(piece) <= limit:
            buffer += piece
            continue
        if buffer:
            chunks.append(buffer)
            buffer = ""
        while len(piece) > limit:
            chunks.append(piece[:limit])
            piece = piece[limit:]
        buffer = piece
    if buffer:
        chunks.append(buffer)
    return chunks


def _paragraphs(body: str) -> list[str]:
    result: list[str] = []
    for raw in re.split(r"\n\s*\n", body):
        text = re.sub(r"\s+", " ", " ".join(line.strip() for line in raw.splitlines())).strip()
        if text:
            result.append(text)
    return result


def _read_markdown(path: Path) -> tuple[str, str, list[tuple[str, str]]]:
    """Return (document title, origin note, [(section title, section body), ...])."""
    title = path.stem
    origin = ""
    sections: list[tuple[str, str]] = []
    current_title: str | None = None
    buffer: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# ") and current_title is None:
            title = line[2:].strip()
        elif line.startswith("> ") and current_title is None and not origin:
            origin = line.lstrip("> ").strip()
        elif line.startswith("## "):
            if current_title is not None:
                sections.append((current_title, "\n".join(buffer)))
            current_title = line[3:].strip()
            buffer = []
        elif current_title is not None:
            buffer.append(line)
    if current_title is not None:
        sections.append((current_title, "\n".join(buffer)))
    return title, origin, sections


def build_chunks(knowledge_dir: Path | None = None) -> list[dict]:
    directory = knowledge_dir or settings.knowledge_dir
    files = sorted(directory.glob("*.md"))
    if not files:
        raise FileNotFoundError(f"知识库目录中没有 Markdown 文件：{directory}")

    chunks: list[dict] = []
    for path in files:
        _, origin, sections = _read_markdown(path)
        for section_no, (section_title, body) in enumerate(sections, 1):
            paragraphs = _paragraphs(body)
            if not paragraphs:
                continue
            part_no = 0
            for paragraph in paragraphs:
                for part in _split_long_text(paragraph):
                    part_no += 1
                    chunks.append({
                        "id": f"{path.stem}-{section_no:02d}-{part_no:02d}",
                        "source": path.name,
                        "origin": origin,
                        "title": section_title,
                        "text": part,
                        "chars": len(part),
                    })
    return chunks


def save_chunks(chunks: list[dict], output: Path | None = None) -> Path:
    target = output or settings.knowledge_chunks_path
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "chunk_size_limit": MAX_CHUNK_CHARS,
        "overlap": CHUNK_OVERLAP,
        "chunk_count": len(chunks),
        "chunks": chunks,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main() -> None:
    chunks = build_chunks()
    by_source: dict[str, list[dict]] = {}
    for chunk in chunks:
        by_source.setdefault(chunk["source"], []).append(chunk)

    print(f"知识库目录：{settings.knowledge_dir}")
    for source, items in by_source.items():
        lengths = [item["chars"] for item in items]
        print(f"  {source}：{len(items)} 个片段，平均 {sum(lengths) // len(lengths)} 字，最长 {max(lengths)} 字")
    lengths = [chunk["chars"] for chunk in chunks]
    print(f"合计：{len(by_source)} 个文件，{len(chunks)} 个片段，"
          f"平均 {sum(lengths) // len(lengths)} 字，最长 {max(lengths)} 字，重叠 {CHUNK_OVERLAP} 字")
    target = save_chunks(chunks)
    print(f"已写入：{target}")


if __name__ == "__main__":
    main()
