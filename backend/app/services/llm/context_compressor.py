"""
Context compression: summarize old chat history to save tokens.

Strategy:
- Keep last RECENT_MESSAGES_COUNT messages in full (they're most relevant)
- Summarize older messages into a compact summary paragraph
- Tool descriptions are trimmed to max length

This typically reduces 20-message history from ~5000 tokens to ~1500.
"""
import logging

from app.providers.base import BaseProvider

logger = logging.getLogger(__name__)

# How many recent messages to keep in full
RECENT_MESSAGES_FULL = 6

# Max chars for summarized history block
SUMMARY_MAX_CHARS = 800

# Max chars per tool description (in JSON schema)
TOOL_DESC_MAX_CHARS = 100

HISTORY_SUMMARY_PROMPT = """Сожми историю диалога в краткое резюме (максимум 3-4 предложения).
Сохрани: ключевые вопросы пользователя, важные факты, результаты действий.
Отбрось: приветствия, повторы, промежуточные рассуждения.

Диалог:
{history}

Краткое резюме:"""


async def compress_history(
    messages: list[dict],
    provider: BaseProvider,
    model_name: str,
) -> list[dict]:
    """
    Compress message history: summarize old messages, keep recent in full.

    Input: list of {"role": ..., "content": ...} from DB (without system message)
    Output: compressed list — summary message + recent messages
    """
    if len(messages) <= RECENT_MESSAGES_FULL:
        # Short history — no need to compress
        return messages

    # Split into old (to summarize) and recent (to keep)
    old_messages = messages[:-RECENT_MESSAGES_FULL]
    recent_messages = messages[-RECENT_MESSAGES_FULL:]

    # Build text for summarization
    history_lines = []
    total_chars = 0
    for m in old_messages:
        role_label = "Пользователь" if m["role"] == "user" else "Ассистент"
        content = m.get("content", "")
        # Truncate very long messages for the summary prompt
        if len(content) > 500:
            content = content[:500] + "..."
        line = f"{role_label}: {content}"
        history_lines.append(line)
        total_chars += len(line)
        # Don't feed more than 4000 chars to summarizer
        if total_chars > 4000:
            break

    history_text = "\n".join(history_lines)

    try:
        prompt = HISTORY_SUMMARY_PROMPT.format(history=history_text)
        resp = await provider.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model_name,
            temperature=0.2,
            max_tokens=300,
        )
        summary = resp.content.strip()
        if len(summary) > SUMMARY_MAX_CHARS:
            summary = summary[:SUMMARY_MAX_CHARS] + "..."

        logger.info(
            f"History compressed: {len(old_messages)} old messages → {len(summary)} chars summary, "
            f"keeping {len(recent_messages)} recent messages"
        )

        # Build compressed history: summary as system-like context + recent messages
        compressed = [
            {
                "role": "user",
                "content": f"[Краткое содержание предыдущего диалога]\n{summary}",
            },
            {
                "role": "assistant",
                "content": "Понял, учитываю контекст предыдущего диалога.",
            },
        ]
        compressed.extend(recent_messages)
        return compressed

    except Exception as e:
        logger.warning(f"History compression failed, using truncated history: {e}")
        # Fallback: just keep recent messages without summary
        return recent_messages


def trim_tool_definitions(tool_defs: list[dict] | None) -> list[dict] | None:
    """
    Trim tool description lengths to reduce token usage.
    Keeps function name and parameters intact, but trims description.
    """
    if not tool_defs:
        return tool_defs

    trimmed = []
    for tool in tool_defs:
        tool = _deep_copy_tool(tool)
        # Trim top-level function description
        func = tool.get("function", tool)
        desc = func.get("description", "")
        if len(desc) > TOOL_DESC_MAX_CHARS:
            func["description"] = desc[:TOOL_DESC_MAX_CHARS] + "..."

        # Trim parameter descriptions
        params = func.get("parameters", {})
        props = params.get("properties", {})
        for prop_name, prop_val in props.items():
            if isinstance(prop_val, dict):
                prop_desc = prop_val.get("description", "")
                if len(prop_desc) > TOOL_DESC_MAX_CHARS:
                    prop_val["description"] = prop_desc[:TOOL_DESC_MAX_CHARS] + "..."

        trimmed.append(tool)

    return trimmed


def _deep_copy_tool(tool: dict) -> dict:
    """Simple deep copy for tool dict (avoids mutating originals)."""
    import json
    return json.loads(json.dumps(tool))
