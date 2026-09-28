"""Storyboard generation adapted from ai_story's staged LLM processing approach."""

import json
import re
import urllib.error
import urllib.request

from app.config import settings
from app.schemas import StoryboardCreate


SYSTEM_PROMPT = """你是竖屏漫剧分镜编剧。只输出一个 JSON 对象，不要 Markdown，格式为
{"storyboards": [...]}。storyboards 的每个元素必须包含：
sequence（从1连续递增的整数）、scene_description（可拍摄的画面描述）、
dialogue（旁白或台词，可为空）、emotion（简短情绪）、duration（1到30秒）、
image_prompt（适合文生图的中文提示词）。必须忠于原剧情，不补写关键事实。
至少输出3个镜头，总时长尽量接近用户目标时长。"""


class StoryboardGenerationError(RuntimeError):
    pass


def _extract_json(text: str) -> list[dict]:
    cleaned = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start < 0 or end < start:
            raise StoryboardGenerationError("模型没有返回 JSON 数组") from exc
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as nested_exc:
            raise StoryboardGenerationError("模型返回的 JSON 无法解析") from nested_exc
    if isinstance(value, dict):
        value = value.get("storyboards") or value.get("shots") or value.get("data")
    if not isinstance(value, list):
        raise StoryboardGenerationError("模型返回结果不是分镜数组")
    return value


def _validate(items: list[dict]) -> list[StoryboardCreate]:
    try:
        result = [StoryboardCreate.model_validate(item) for item in items]
    except Exception as exc:
        raise StoryboardGenerationError(f"分镜字段校验失败：{exc}") from exc
    if len(result) < 3:
        raise StoryboardGenerationError("模型生成的分镜少于 3 个")
    if [item.sequence for item in result] != list(range(1, len(result) + 1)):
        raise StoryboardGenerationError("模型生成的分镜序号不连续")
    return result


def _request_once(story_text: str, art_style: str, target_duration: int) -> list[StoryboardCreate]:
    if not settings.deepseek_api_key:
        raise StoryboardGenerationError("未配置 DEEPSEEK_API_KEY，可先使用演示分镜")
    body = json.dumps({
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"画风：{art_style}\n目标时长：{target_duration}秒\n剧情：\n{story_text}"},
        ],
        "temperature": 0.5,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=55) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        parsed = _extract_json(content)
        return _validate(parsed)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise StoryboardGenerationError(f"DeepSeek 请求失败（{exc.code}）：{detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise StoryboardGenerationError(f"DeepSeek 暂时不可用：{exc}") from exc
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise StoryboardGenerationError("DeepSeek 返回结构异常") from exc


def generate_storyboards(story_text: str, art_style: str, target_duration: int) -> list[StoryboardCreate]:
    """Generate and validate, retrying once as required by AC-01/UC-03."""
    first_error: Exception | None = None
    for _ in range(2):
        try:
            return _request_once(story_text, art_style, target_duration)
        except StoryboardGenerationError as exc:
            first_error = exc
    raise StoryboardGenerationError(str(first_error))


def demo_storyboards(story_text: str, art_style: str, target_duration: int) -> list[StoryboardCreate]:
    """Deterministic offline path for demos and API outage recovery."""
    fragments = [part.strip() for part in re.split(r"[。！？\n]+", story_text) if part.strip()]
    if len(fragments) < 3:
        width = max(1, len(story_text) // 3)
        fragments = [story_text[i : i + width].strip() for i in range(0, len(story_text), width) if story_text[i : i + width].strip()]
    count = max(3, min(12, len(fragments)))
    duration = max(1, min(30, round(target_duration / count, 1)))
    return [StoryboardCreate(
        sequence=index,
        scene_description=fragment,
        dialogue=fragment,
        emotion="待确认",
        duration=duration,
        image_prompt=f"{art_style}，9:16竖屏构图，{fragment}",
    ) for index, fragment in enumerate(fragments[:count], 1)]
