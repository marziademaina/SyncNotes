from datetime import datetime

from pydantic import BaseModel


class UploadRequest(BaseModel):
    content: str
    base_version: int | None = None
    op_id: str | None = None


class DeleteRequest(BaseModel):
    op_id: str | None = None


class FileResponse(BaseModel):
    name: str
    version: int
    content: str
    content_hash: str
    updated_at: datetime
    had_conflict: bool = False
    deleted: bool = False


class FileSummary(BaseModel):
    name: str
    version: int
    content_hash: str
    updated_at: datetime


class HealthResponse(BaseModel):
    status: str
    replica_id: str
    state: int
    leader: str | None
    has_quorum: bool
    peers_connected: int = 0
    peers_total: int = 0
