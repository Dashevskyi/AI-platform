"""
File storage service for message attachments.
Stores files locally under UPLOAD_DIR. Can be extended to S3.
"""
import os
import uuid
import logging

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/home/ai-platform/backend/uploads")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


async def save_file(tenant_id: str, chat_id: str, filename: str, content: bytes) -> str:
    """
    Save file to local storage.
    Returns the storage path relative to UPLOAD_DIR.
    """
    safe_name = f"{uuid.uuid4().hex}_{filename}"
    rel_dir = os.path.join(str(tenant_id), str(chat_id))
    full_dir = os.path.join(UPLOAD_DIR, rel_dir)
    _ensure_dir(full_dir)

    full_path = os.path.join(full_dir, safe_name)
    with open(full_path, "wb") as f:
        f.write(content)

    storage_path = os.path.join(rel_dir, safe_name)
    logger.info(f"Saved file: {storage_path} ({len(content)} bytes)")
    return storage_path


async def read_file(storage_path: str) -> bytes:
    """Read file from storage."""
    full_path = os.path.join(UPLOAD_DIR, storage_path)
    with open(full_path, "rb") as f:
        return f.read()


def get_file_type(filename: str) -> str:
    """Determine file type from filename."""
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return "pdf"
    elif lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff")):
        return "image"
    elif lower.endswith((".mp3", ".wav", ".ogg", ".flac", ".m4a", ".wma", ".aac", ".webm")):
        return "audio"
    elif lower.endswith(".docx"):
        return "docx"
    elif lower.endswith((".xlsx", ".xls")):
        return "xlsx"
    elif lower.endswith((".csv",)):
        return "csv"
    elif lower.endswith((".json",)):
        return "json"
    elif lower.endswith((".html", ".htm")):
        return "html"
    elif lower.endswith((".xml",)):
        return "xml"
    elif lower.endswith((".md", ".txt", ".log")):
        return "text"
    else:
        return "text"
