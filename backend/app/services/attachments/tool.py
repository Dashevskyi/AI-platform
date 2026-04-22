"""
Built-in tool definitions for attachment search.
Dynamically registered in the pipeline when attachments are present.
"""
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.attachments.processor import search_attachment_chunks

logger = logging.getLogger(__name__)


def build_attachment_tool_def(attachment_id: str, filename: str, summary: str) -> dict:
    """Build OpenAI-format tool definition for searching an attachment."""
    safe_name = f"search_attachment_{attachment_id[:8]}"
    return {
        "type": "function",
        "function": {
            "name": safe_name,
            "description": (
                f"Поиск по содержимому файла '{filename}'. "
                f"Краткое описание: {summary or 'нет описания'}. "
                f"Используй для нахождения конкретной информации в этом файле."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос для поиска релевантных фрагментов в файле",
                    },
                },
                "required": ["query"],
            },
        },
    }


async def execute_attachment_search(
    attachment_id: str,
    query: str,
    db: AsyncSession,
    provider,
    embedding_model: str,
) -> str:
    """Execute semantic search within an attachment and return formatted results."""
    try:
        chunks = await search_attachment_chunks(
            attachment_id=attachment_id,
            query=query,
            db=db,
            provider=provider,
            embedding_model=embedding_model,
            max_results=5,
        )

        if not chunks:
            return "Релевантных фрагментов не найдено."

        results = []
        for i, chunk in enumerate(chunks, 1):
            results.append(f"--- Фрагмент {i} ---\n{chunk.content}")

        return "\n\n".join(results)

    except Exception as e:
        logger.error(f"Attachment search failed: {e}")
        return f"Ошибка поиска по файлу: {str(e)[:200]}"
