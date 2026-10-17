"""Retrieval-augmented Q&A for the ManJv usage assistant.

Retrieval is keyword based: Chinese bigrams plus ASCII words are extracted
from the question and matched against every chunk. A title hit gets a small
bonus, and a minimum score decides whether the result is relevant enough to
answer. The module introduces no third-party dependency; model access reuses
urllib and app.config.settings.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from app.config import settings


DEFAULT_K = 3
MIN_RELEVANCE = 2.0
REQUEST_TIMEOUT = 30
MAX_ANSWER_TOKENS = 800
TEMPERATURE = 0.3

SYSTEM_PROMPT_RAG = """你是“漫剧工厂”软件的使用助手，只解答与这款软件有关的问题。请遵守：
1. 只根据“资料”中的内容回答，不补充资料没有的事实；回答中的每个结论末尾用 [来源n] 标注依据。
2. 资料不足以回答时，直接说“资料不足”，并说明缺少哪方面信息，不要编造。
3. 问题与软件使用无关时，说明自己只解答本软件的使用问题，并给出一个可回答的示例。
4. 问题描述不完整时先追问缺失信息，例如只说“报错了”时，追问错误提示、使用的模式或作品状态。
5. 回答用简洁的中文，先给结论，再给必要步骤，不使用 Markdown 加粗、代码块或表格。"""

SYSTEM_PROMPT_V1 = """你是“漫剧工厂”软件的使用助手，请根据资料回答用户问题。"""

SYSTEM_PROMPT_DIRECT = """你是“漫剧工厂”软件的使用助手，请回答用户问题。回答用简洁的中文，不使用 Markdown 加粗、代码块或表格。"""

CITATION_RE = re.compile(r"\[来源(\d+)\]")
_ASCII_RE = re.compile(r"[a-z][a-z0-9_]*")
_CJK_RE = re.compile(r"[一-鿿]+")

# 疑问词、连接词与高频泛化词不参与计分，否则“怎么”“软件”会把无关片段顶上来。
STOP_FEATURES = {
    "这个", "那个", "这些", "那些", "什么", "怎么", "如何", "是否", "哪些", "多少",
    "哪里", "为什么", "为什", "怎样", "怎么样", "好吗", "可以", "能否", "能不能",
    "帮我", "我的", "一下", "的话", "还是", "就是", "没有", "因为", "所以", "但是",
    "然后", "现在", "时候", "以及", "或者", "并且", "而且", "比较", "非常", "真的",
    "觉得", "认为", "希望", "想要", "看看", "说说", "介绍", "详细", "具体",
    "软件", "使用", "功能", "支持", "问题", "一个", "进行", "已经", "如果", "不能",
    "不会", "可能", "知道", "告诉", "请问", "有没", "没什", "么样",
}

_CLARIFY_HINTS = ("好吗", "怎么样", "好用吗", "值得", "如何呢", "有什么用")


class RagError(RuntimeError):
    """检索或回答失败时抛出。"""


def load_chunks() -> list[dict]:
    path = settings.knowledge_chunks_path
    if not path.exists():
        raise RagError("知识库尚未构建，请先运行 python -m app.rag_rebuild")
    payload = json.loads(path.read_text(encoding="utf-8"))
    chunks = payload.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise RagError("知识库文件没有可用片段")
    return chunks


def _features(text: str) -> set[str]:
    """Chinese bigrams plus ASCII words, with generic features removed."""
    lowered = text.lower()
    result = {word for word in _ASCII_RE.findall(lowered) if len(word) >= 2}
    for run in _CJK_RE.findall(lowered):
        if len(run) == 1:
            result.add(run)
        else:
            result.update(run[index:index + 2] for index in range(len(run) - 1))
    return result - STOP_FEATURES


def rank_chunks(question: str) -> list[tuple[float, dict]]:
    """Score every chunk; a title match is worth one body match."""
    query = _features(question)
    if not query:
        return []
    ranked: list[tuple[float, dict]] = []
    for chunk in load_chunks():
        body_hits = query & _features(chunk["text"])
        title_hits = query & _features(f"{chunk['title']} {chunk.get('origin', '')}")
        score = len(body_hits) + len(title_hits)
        if score > 0:
            ranked.append((round(score, 2), chunk))
    ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
    return ranked


def retrieve(question: str, k: int = DEFAULT_K) -> list[dict] | None:
    """Return the top-k relevant chunks, or None when nothing clears the threshold."""
    ranked = rank_chunks(question)
    relevant = [(score, chunk) for score, chunk in ranked if score >= MIN_RELEVANCE]
    if not relevant:
        return None
    return [dict(chunk, score=score) for score, chunk in relevant[:k]]


def classify_intent(question: str, ranked: list[tuple[float, dict]]) -> str:
    """answered / clarify / insufficient, decided before spending a model call."""
    text = question.strip()
    if any(hint in text for hint in _CLARIFY_HINTS) and len(text) <= 14:
        return "clarify"
    if not ranked or ranked[0][0] < MIN_RELEVANCE:
        return "insufficient"
    return "answered"


def build_user_prompt(question: str, chunks: list[dict]) -> str:
    parts = [
        f"[来源{index}] （{chunk['source']}）{chunk['title']}\n{chunk['text']}"
        for index, chunk in enumerate(chunks, 1)
    ]
    return "资料：\n" + "\n---\n".join(parts) + f"\n\n用户问题：{question}"


def call_model(
    messages: list[dict],
    *,
    temperature: float = TEMPERATURE,
    max_tokens: int = MAX_ANSWER_TOKENS,
    retries: int = 1,
    timeout: int = REQUEST_TIMEOUT,
) -> tuple[str, dict]:
    """Call DeepSeek once, retrying transient failures once.

    HTTP 4xx other than 429 are configuration errors and are not retried.
    """
    if not settings.deepseek_api_key:
        raise RagError("未配置 DEEPSEEK_API_KEY，无法调用模型")
    body = json.dumps({
        "model": settings.deepseek_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    url = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
    last_error: RagError | None = None

    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise RagError("模型返回了空内容")
            usage = payload.get("usage") or {}
            meta = {
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
                "model": payload.get("model", settings.deepseek_model),
                "attempts": attempt + 1,
            }
            return content.strip(), meta
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            last_error = RagError(f"接口返回错误（{exc.code}）{detail}")
            if exc.code in {429, 500, 502, 503, 504} and attempt < retries:
                time.sleep(1)
                continue
            raise last_error from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = RagError(f"连接失败或超时：{exc}")
            if attempt < retries:
                time.sleep(1)
                continue
            raise last_error from exc
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise RagError("返回结构异常") from exc
    raise last_error or RagError("模型调用失败")


def check_citations(answer: str, source_count: int) -> dict:
    """Parse [来源n] markers and reject numbers outside the supplied sources."""
    cited = sorted({int(number) for number in CITATION_RE.findall(answer)})
    invalid = [number for number in cited if number < 1 or number > source_count]
    return {"cited": cited, "invalid": invalid, "ok": bool(cited) and not invalid}


def _short_circuit(question: str, answer: str, intent: str) -> dict:
    return {
        "question": question,
        "answer": answer,
        "intent": intent,
        "sources": [],
        "chunks": [],
        "citations": {"cited": [], "invalid": [], "ok": None},
        "messages": [],
        "context_chars": 0,
        "elapsed_ms": 0,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "model": "",
        "attempts": 0,
    }


def ask(
    question: str,
    k: int = DEFAULT_K,
    prompt: str = SYSTEM_PROMPT_RAG,
    temperature: float = TEMPERATURE,
) -> dict:
    """Answer one question with retrieved context.

    Returns a dict with answer, intent, sources, chunks, citations, messages,
    context_chars, elapsed_ms, usage, model and attempts. A question with no
    relevant chunk is answered locally and costs no model call.
    """
    text = question.strip()
    if not text:
        raise RagError("问题不能为空")
    ranked = rank_chunks(text)
    intent = classify_intent(text, ranked)
    if intent == "clarify":
        return _short_circuit(
            text,
            "请补充你想了解的具体方面，例如启动与端口、分镜生成与确认、数据保存位置或常见故障。",
            intent,
        )
    if intent == "insufficient":
        return _short_circuit(
            text,
            "资料不足，无法回答这个问题。使用助手只解答漫剧工厂的使用问题，例如启动方式、分镜生成与确认、数据保存位置。",
            intent,
        )

    relevant = [(score, chunk) for score, chunk in ranked if score >= MIN_RELEVANCE]
    chunks = [dict(chunk, score=score) for score, chunk in relevant[:k]]
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": build_user_prompt(text, chunks)},
    ]
    answer, meta = call_model(messages, temperature=temperature)
    citations = check_citations(answer, len(chunks))
    return {
        "question": text,
        "answer": answer,
        "intent": "answered",
        "sources": [
            {"index": index, "source": chunk["source"], "title": chunk["title"], "score": chunk["score"]}
            for index, chunk in enumerate(chunks, 1)
        ],
        "chunks": chunks,
        "citations": citations,
        "messages": messages,
        "context_chars": len(messages[0]["content"]) + len(messages[1]["content"]),
        **meta,
    }


def ask_direct(question: str, temperature: float = TEMPERATURE) -> dict:
    """Baseline group: same model and temperature, no retrieved material."""
    text = question.strip()
    if not text:
        raise RagError("问题不能为空")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_DIRECT},
        {"role": "user", "content": text},
    ]
    answer, meta = call_model(messages, temperature=temperature)
    return {
        "question": text,
        "answer": answer,
        "intent": "answered",
        "sources": [],
        "chunks": [],
        "citations": {"cited": [], "invalid": [], "ok": None},
        "messages": messages,
        "context_chars": len(messages[0]["content"]) + len(messages[1]["content"]),
        **meta,
    }
