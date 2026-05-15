"""Promote a processed MessageAttachment to a first-class Artifact.

Why: artifacts and attachments solve the same problem from two ends.
  • Artifacts: things the assistant generated (scripts, configs) — need to
    survive context-window eviction so future turns can ground on them.
  • Attachments: things the user uploaded (PDFs, photos) — same need.

Before this module, the two paths used different storage, different
retrieval (artifacts: auto-grounding by embedding; attachments: per-file
search_attachment_<id> tool), different visibility in UI. After: both flow
through the artifacts table, both get the same auto-grounding, and the
search_attachment_* tools remain as a targeted fallback for granular chunk
lookup inside one document.

The existing message_attachments/message_attachment_chunks tables stay —
they store the raw bytes path, processing status, and per-chunk embeddings
used by search_attachment_*. The artifact is the *unified retrieval handle*
on top of them.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from app.core.config import settings as app_settings
from app.core.database import async_session
from app.models.artifact import Artifact
from app.models.message_attachment import MessageAttachment
from app.providers.factory import get_provider
from app.services.memory.embedder import _resolve_embedding_model

logger = logging.getLogger(__name__)


# Don't promote attachments smaller than this to an artifact — under that
# the inline preview in the message itself already carries the content.
MIN_ATTACHMENT_ARTIFACT_CHARS = 100
# Hard cap on stored content. Search-by-chunks (search_attachment_*) handles
# anything past this for users who need granular results.
MAX_ATTACHMENT_ARTIFACT_CHARS = 12000


# file_type (from get_file_type) → artifact kind. Stays close to extractor's
# _LANG_TO_KIND vocabulary so the UI panel groups things consistently.
_FILE_TYPE_TO_KIND: dict[str, str] = {
    "pdf": "pdf-document",
    "image": "image",
    "audio": "audio-transcript",
    "docx": "word-document",
    "xlsx": "spreadsheet",
    "csv": "csv-table",
    "json": "json-config",
    "html": "document",
    "xml": "document",
    "text": "document",
}


def _attachment_kind(file_type: str | None) -> str:
    if not file_type:
        return "document"
    return _FILE_TYPE_TO_KIND.get(file_type.lower(), "document")


def _attachment_label(att: MessageAttachment) -> str:
    """Use the LLM-generated summary as the label when we have one — it's
    the most informative short description we own. Fall back to filename."""
    summary = (att.summary or "").strip()
    if summary and len(summary) <= 500:
        return summary
    if summary:
        return summary[:200].rstrip() + "…"
    return att.filename or "(без имени)"


async def upsert_artifact_from_attachment(
    *,
    attachment_id: uuid.UUID,
) -> uuid.UUID | None:
    """Create or update the Artifact row representing this attachment.
    Returns the artifact id on success, None on skip/failure. Idempotent:
    re-processing the same attachment updates the existing artifact in place
    (rather than spawning a new version chain).
    """
    try:
        async with async_session() as db:
            from sqlalchemy import select
            att = (await db.execute(
                select(MessageAttachment).where(MessageAttachment.id == attachment_id)
            )).scalar_one_or_none()
            if not att or att.processing_status != "done":
                return None
            content_text = (att.content_text or "").strip()
            if len(content_text) < MIN_ATTACHMENT_ARTIFACT_CHARS:
                return None

            content = content_text if len(content_text) <= MAX_ATTACHMENT_ARTIFACT_CHARS \
                else content_text[:MAX_ATTACHMENT_ARTIFACT_CHARS] + "\n…[усечено — используй search_attachment для полного поиска]"
            kind = _attachment_kind(att.file_type)
            label = _attachment_label(att)

            # Try to find an existing artifact already mapped to this
            # attachment (linked via the deterministic `source_message_id` +
            # a tag in content). We keep it simple: search by chat + kind +
            # exact filename match in label OR same source_message_id with
            # an attachment-kind. Re-processing the same file just updates.
            existing = (await db.execute(
                select(Artifact).where(
                    Artifact.tenant_id == att.tenant_id,
                    Artifact.chat_id == att.chat_id,
                    Artifact.deleted_at.is_(None),
                    Artifact.kind == kind,
                    Artifact.source_message_id == att.message_id,
                )
            )).scalars().first()

            # Compute embedding once for the new content.
            embed_model = await _resolve_embedding_model(att.tenant_id, db)
            vec: list[float] | None = None
            if embed_model:
                try:
                    provider = get_provider(
                        "ollama",
                        app_settings.OLLAMA_BASE_URL or "http://localhost:11434",
                    )
                    embed_input = f"{label}\n{content[:1500]}"
                    vectors = await provider.embed(embed_input, embed_model)
                    if vectors:
                        vec = vectors[0]
                except Exception:
                    logger.exception("[attachment-artifact] embed failed for %s", attachment_id)

            now = datetime.now(timezone.utc)
            if existing is not None:
                existing.content = content
                existing.label = label[:500]
                existing.tokens_estimate = max(1, len(content) // 4)
                existing.last_referenced_at = now
                if vec is not None:
                    existing.embedding = vec
                    existing.embedding_model = embed_model
                await db.commit()
                logger.info("[attachment-artifact] updated %s for attachment %s", existing.id, attachment_id)
                return existing.id

            art = Artifact(
                tenant_id=att.tenant_id,
                chat_id=att.chat_id,
                source_message_id=att.message_id,
                kind=kind,
                label=label[:500],
                lang=att.file_type,
                content=content,
                tokens_estimate=max(1, len(content) // 4),
                version=1,
                parent_artifact_id=None,
                last_referenced_at=now,
            )
            if vec is not None:
                art.embedding = vec
                art.embedding_model = embed_model
            db.add(art)
            await db.commit()
            await db.refresh(art)
            logger.info(
                "[attachment-artifact] created %s for attachment %s (%s, %d chars)",
                art.id, attachment_id, kind, len(content),
            )
            return art.id
    except Exception:
        logger.exception("[attachment-artifact] failed for attachment %s", attachment_id)
        return None
