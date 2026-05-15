from datetime import datetime

from pydantic import BaseModel


class ArtifactBrief(BaseModel):
    """List-view shape — content excluded so listing 100 artifacts stays fast.
    The UI fetches `ArtifactDetail` for the one the user expands."""
    id: str
    chat_id: str
    source_message_id: str | None
    kind: str
    label: str
    lang: str | None
    version: int
    parent_artifact_id: str | None
    tokens_estimate: int
    last_referenced_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ArtifactDetail(ArtifactBrief):
    content: str
