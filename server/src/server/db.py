import hashlib
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from server.merge import resolve_conflict

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "./data/server.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class FileRecord(Base):
    __tablename__ = "files"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AppliedOperation(Base):
    __tablename__ = "applied_operations"

    op_id: Mapped[str] = mapped_column(String, primary_key=True)


class FileVersion(Base):
    __tablename__ = "file_versions"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(Text)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_add_deleted_column()


def _migrate_add_deleted_column() -> None:
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(files)"))}
        if "deleted" not in columns:
            conn.execute(text("ALTER TABLE files ADD COLUMN deleted BOOLEAN NOT NULL DEFAULT 0"))


def get_session() -> Session:
    return SessionLocal()


def apply_if_newer(
    name: str, version: int, content: str, content_hash: str, force: bool = False, deleted: bool | None = None
) -> tuple[str, int]:
    session = get_session()
    try:
        existing = session.get(FileRecord, name)
        should_apply = (
            force
            or existing is None
            or version > existing.version
            or (version == existing.version and content_hash != existing.content_hash)
        )
        if not should_apply:
            return "ignored", existing.version

        if existing:
            existing.version = version
            existing.content = content
            existing.content_hash = content_hash
            if deleted is not None:
                existing.deleted = deleted
        else:
            existing = FileRecord(
                name=name, version=version, content=content, content_hash=content_hash, deleted=bool(deleted)
            )
            session.add(existing)

        session.commit()
        return "applied", version
    finally:
        session.close()


def commit_write(op_id: str, name: str, content: str, updated_at: datetime, base_version: int | None = None) -> dict:
    session = get_session()
    try:
        existing = session.get(FileRecord, name)
        op_seen = session.get(AppliedOperation, op_id) is not None
        if op_seen and existing is not None and not existing.deleted:
            return _file_record_dict(existing, had_conflict=False)

        if not op_seen:
            session.add(AppliedOperation(op_id=op_id))

        base_content = None
        if base_version is not None:
            base_record = session.get(FileVersion, (name, base_version))
            base_content = base_record.content if base_record is not None else None

        version = (existing.version + 1) if existing else 1
        resurrecting = existing is not None and existing.deleted
        authoritative_content = None if (existing is None or resurrecting) else existing.content
        merged_content, had_conflict = resolve_conflict(base_content, authoritative_content, content)
        content_hash = hashlib.sha256(merged_content.encode()).hexdigest()

        if had_conflict:
            logger.warning(
                "merge conflict on %r: edit based on version %s overlapped a concurrent change - "
                "the overlapping lines from this upload were dropped",
                name,
                base_version,
            )

        if existing:
            existing.version = version
            existing.content = merged_content
            existing.content_hash = content_hash
            existing.updated_at = updated_at
            existing.deleted = False
            record = existing
        else:
            record = FileRecord(
                name=name, version=version, content=merged_content, content_hash=content_hash, updated_at=updated_at
            )
            session.add(record)

        session.add(FileVersion(name=name, version=version, content=merged_content))

        session.commit()
        session.refresh(record)
        return _file_record_dict(record, had_conflict)
    finally:
        session.close()


def commit_delete(op_id: str, name: str, updated_at: datetime) -> dict:
    session = get_session()
    try:
        existing = session.get(FileRecord, name)
        if existing is not None and existing.deleted:
            return _file_record_dict(existing, had_conflict=False)

        if session.get(AppliedOperation, op_id) is None:
            session.add(AppliedOperation(op_id=op_id))

        if existing is None:
            empty_hash = hashlib.sha256(b"").hexdigest()
            record = FileRecord(
                name=name, version=1, content="", content_hash=empty_hash, updated_at=updated_at, deleted=True
            )
            session.add(record)
            session.add(FileVersion(name=name, version=1, content=""))
        else:
            existing.version += 1
            existing.updated_at = updated_at
            existing.deleted = True
            record = existing
            session.add(FileVersion(name=name, version=record.version, content=record.content))

        session.commit()
        session.refresh(record)
        return _file_record_dict(record, had_conflict=False)
    finally:
        session.close()


def _file_record_dict(record: FileRecord, had_conflict: bool) -> dict:
    return {
        "name": record.name,
        "version": record.version,
        "content": record.content,
        "content_hash": record.content_hash,
        "updated_at": record.updated_at.isoformat(),
        "had_conflict": had_conflict,
        "deleted": record.deleted,
    }


def list_files() -> dict[str, dict]:
    session = get_session()
    try:
        records = session.scalars(select(FileRecord)).all()
        return {
            r.name: {"version": r.version, "content_hash": r.content_hash, **({"deleted": True} if r.deleted else {})}
            for r in records
        }
    finally:
        session.close()


def list_files_summary() -> list[dict]:
    session = get_session()
    try:
        records = session.scalars(
            select(FileRecord).where(FileRecord.deleted.is_(False)).order_by(FileRecord.name)
        ).all()
        return [
            {
                "name": r.name,
                "version": r.version,
                "content_hash": r.content_hash,
                "updated_at": r.updated_at.isoformat(),
            }
            for r in records
        ]
    finally:
        session.close()


def find_locally_corrupted_files() -> set[str]:
    session = get_session()
    try:
        records = session.scalars(select(FileRecord)).all()
        return {r.name for r in records if hashlib.sha256(r.content.encode()).hexdigest() != r.content_hash}
    finally:
        session.close()
