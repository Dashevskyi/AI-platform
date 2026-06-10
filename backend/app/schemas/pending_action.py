from datetime import datetime

from pydantic import BaseModel

from app.models.pending_tool_action import PendingToolAction


class PendingActionResponse(BaseModel):
    id: str
    chat_id: str
    tool_name: str
    command_name: str | None
    command_text: str | None
    status: str
    result_text: str | None
    error_text: str | None
    created_at: datetime
    decided_at: datetime | None


def to_response(a: PendingToolAction) -> PendingActionResponse:
    return PendingActionResponse(
        id=str(a.id),
        chat_id=str(a.chat_id),
        tool_name=a.tool_name,
        command_name=a.command_name,
        command_text=a.command_text,
        status=a.status,
        result_text=a.result_text,
        error_text=a.error_text,
        created_at=a.created_at,
        decided_at=a.decided_at,
    )
