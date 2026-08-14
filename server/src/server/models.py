from datetime import datetime

from pydantic import BaseModel


class UploadRequest(BaseModel):
    content: str
    base_version: int | None = None


class FileResponse(BaseModel):
    name: str
    version: int
    content: str
    content_hash: str
    updated_at: datetime


class HealthResponse(BaseModel):
    status: str
    replica_id: str
    state: int
    leader: str | None
    has_quorum: bool
