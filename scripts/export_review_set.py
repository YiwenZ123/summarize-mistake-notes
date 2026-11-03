#!/usr/bin/env python3
"""Manage course-classified mistake notes in SQLite, with Markdown backup export."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote


EXIT_ERROR = 1
EXIT_NOT_CONFIGURED = 3
EXIT_STORAGE_MISSING = 4
EXIT_CONFIRMATION_REQUIRED = 5


class CliError(Exception):
    exit_code = EXIT_ERROR


class NotConfiguredError(CliError):
    exit_code = EXIT_NOT_CONFIGURED


class StorageMissingError(CliError):
    exit_code = EXIT_STORAGE_MISSING


class ConfirmationRequiredError(CliError):
    exit_code = EXIT_CONFIRMATION_REQUIRED

    def __init__(self, message: str, preview: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.preview = preview


SKILL_NAME = "summarize-mistake-notes"
DEFAULT_CONFIG = Path.home() / ".config" / SKILL_NAME / "config.json"
DB_NAME = "exercise_bank.sqlite3"
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
ALLOWED_COLLECTION_TYPES = {"mistake_set", "question_set"}
LEGACY_COLLECTION_TYPE_ALIASES = {
    "\u9519\u9898\u96c6": "mistake_set",
    "\u95ee\u9898\u96c6": "question_set",
}
REQUIRED_ITEM_TEXT_FIELDS = (
    "title",
    "original_question",
    "mistake_reason",
    "correct_approach",
    "review_suggestion",
)
REQUIRED_ITEM_LIST_FIELDS = ("knowledge_points", "answer_points")
ALLOWED_ATTACHMENT_ROLES = {"prompt", "solution"}
ALLOWED_ATTACHMENT_PROVENANCE = {"provided", "reconstructed"}
ATTACHMENT_MEDIA_TYPES = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
}
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
HASH_CHUNK_SIZE = 1024 * 1024
SCHEMA_VERSION = 2
REVIEW_INTERVAL_DAYS = (1, 3, 7, 14, 30)
REVIEW_RESULTS = ("correct", "partial", "wrong")
HISTORY_LIMIT = 20
EDITABLE_TEXT_FIELDS = (
    "course",
    "topic",
    "collection_type",
    "title",
    "original_question",
    "mistake_reason",
    "correct_approach",
    "review_suggestion",
)
EDITABLE_LIST_FIELDS = ("knowledge_points", "answer_points")
SKILL_OWNED_DIRECTORIES = ("attachments", "exports")
QUESTION_SELECT = """
    SELECT q.*, c.name AS course
    FROM questions q
    JOIN courses c ON c.id = q.course_id
"""
DUE_CONDITION = (
    "q.review_status = 'pending' AND (q.next_review_at IS NULL OR q.next_review_at <= ?)"
)


@dataclass(frozen=True)
class StorageContext:
    db_path: Path
    notes_root: Path
    attachments_root: Path


def fail(message: str, code: int = EXIT_ERROR) -> int:
    print(message, file=sys.stderr)
    return code


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def load_pillow() -> tuple[Any, Any]:
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise CliError(
            "PILLOW_REQUIRED: Image attachments need the Pillow package. "
            f"Install it for this Python with: {sys.executable} -m pip install Pillow"
        ) from exc
    return Image, UnidentifiedImageError


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"Input JSON not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Input JSON is invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Input JSON must be an object.")
    return data


def read_json_arg(raw_path: str) -> dict[str, Any]:
    if raw_path == "-":
        try:
            data = json.loads(sys.stdin.read())
        except json.JSONDecodeError as exc:
            raise ValueError(f"stdin JSON is invalid: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("stdin JSON must be an object.")
        return data
    return read_json(Path(raw_path))


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Config JSON is invalid: {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Config JSON must be an object: {config_path}")
    return data


def save_config(config_path: Path, notes_root: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    notes_root.mkdir(parents=True, exist_ok=True)
    payload = {"notes_root": str(notes_root.resolve())}
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def get_notes_root(config_path: Path, override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    config = load_config(config_path)
    raw = str(config.get("notes_root", "")).strip()
    if not raw:
        raise NotConfiguredError(
            "NOT_CONFIGURED: notes_root is missing. Ask the user for a mistake-note root folder, "
            "then run: config set --notes-root <path>"
        )
    notes_root = Path(raw).expanduser().resolve()
    require_existing_folder(notes_root)
    return notes_root


def require_existing_folder(folder: Path) -> None:
    if not folder.is_dir():
        raise StorageMissingError(
            f"NOTES_ROOT_MISSING: The configured exercise-bank folder is not available: {folder}. "
            "It may have been moved, renamed, disconnected, or not synced yet. Ask the user to restore "
            "it or choose another folder with: config set --notes-root <path>. "
            "Do not create a replacement folder."
        )


def get_db_path(config_path: Path, override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    config = load_config(config_path)
    raw_db = str(config.get("db_path", "")).strip()
    if raw_db:
        db_path = Path(raw_db).expanduser().resolve()
        require_existing_folder(db_path.parent)
        return db_path
    return get_notes_root(config_path) / DB_NAME


def resolve_storage_context(
    config_path: Path,
    db_override: str | None = None,
    notes_override: str | None = None,
) -> StorageContext:
    db_path = get_db_path(config_path, db_override)
    if notes_override:
        notes_root = Path(notes_override).expanduser().resolve()
    elif db_override:
        notes_root = db_path.parent
    else:
        notes_root = get_notes_root(config_path)
    return StorageContext(
        db_path=db_path,
        notes_root=notes_root,
        attachments_root=notes_root / "attachments",
    )


def storage_from_args(args: argparse.Namespace) -> StorageContext:
    return resolve_storage_context(
        args.config, getattr(args, "db_path", None), getattr(args, "notes_root", None)
    )


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def clean_filename(value: Any, fallback: str, max_len: int = 80) -> str:
    text = str(value or "").strip()
    text = INVALID_FILENAME_CHARS.sub("-", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    text = re.sub(r"-{2,}", "-", text)
    if not text:
        text = fallback
    return text[:max_len].strip(" .") or fallback


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def required_text(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key, "")).strip()
    if not value:
        raise ValueError(f"Missing required field: {key}")
    return value


def normalize_collection_type(value: Any) -> str:
    raw = str(value or "").strip()
    if raw in ALLOWED_COLLECTION_TYPES:
        return raw
    return LEGACY_COLLECTION_TYPE_ALIASES.get(raw, "")


def file_digest_and_size(path: Path, *, enforce_limit: bool = False) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            byte_size += len(chunk)
            if enforce_limit and byte_size > MAX_ATTACHMENT_BYTES:
                raise ValueError(f"Attachment exceeds {MAX_ATTACHMENT_BYTES} bytes: {path}")
            digest.update(chunk)
    return digest.hexdigest(), byte_size


def verified_image_type(path: Path) -> tuple[str, str]:
    Image, UnidentifiedImageError = load_pillow()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image.verify()
                image_format = str(image.format or "").upper()
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise ValueError(f"Attachment is not a valid image: {path}") from exc
    if image_format not in ATTACHMENT_MEDIA_TYPES:
        raise ValueError(f"Unsupported attachment image format: {image_format}")
    return ATTACHMENT_MEDIA_TYPES[image_format]


def verify_source_image(source_path: str) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise ValueError(f"Attachment source is not a readable file: {source}")
    digest, byte_size = file_digest_and_size(source, enforce_limit=True)
    media_type, extension = verified_image_type(source)
    return {
        "source": source,
        "sha256": digest,
        "media_type": media_type,
        "extension": extension,
        "byte_size": byte_size,
    }


def validated_attachment_request(raw_attachment: dict[str, Any]) -> dict[str, Any]:
    role = str(raw_attachment.get("role", "")).strip()
    provenance = str(raw_attachment.get("provenance", "")).strip()
    caption = str(raw_attachment.get("caption", "")).strip()
    if role not in ALLOWED_ATTACHMENT_ROLES:
        raise ValueError("Attachment role must be prompt or solution.")
    if provenance not in ALLOWED_ATTACHMENT_PROVENANCE:
        raise ValueError("Attachment provenance must be provided or reconstructed.")
    if not caption:
        raise ValueError("Attachment caption is required.")
    source = Path(str(raw_attachment.get("source_path", "")).strip()).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise ValueError(f"Attachment source is not a readable file: {source}")
    if source.stat().st_size > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"Attachment exceeds {MAX_ATTACHMENT_BYTES} bytes: {source}")
    return {
        "source": source,
        "role": role,
        "provenance": provenance,
        "caption": caption,
        "original_filename": source.name,
    }

def validate_review_data(data: dict[str, Any]) -> None:
    errors: list[str] = []

    course = str(data.get("course", "")).strip()
    if not course:
        errors.append("course is required.")

    collection_type = normalize_collection_type(data.get("collection_type"))
    if not collection_type:
        allowed = " / ".join(sorted(ALLOWED_COLLECTION_TYPES))
        errors.append(f"collection_type must be one of: {allowed}.")

    topic = str(data.get("topic", "")).strip()
    if not topic:
        errors.append("topic is required.")

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        errors.append("items must be a non-empty list.")
    elif isinstance(raw_items, list):
        for index, raw_item in enumerate(raw_items, start=1):
            prefix = f"items[{index}]"
            if not isinstance(raw_item, dict):
                errors.append(f"{prefix} must be an object.")
                continue
            for key in REQUIRED_ITEM_TEXT_FIELDS:
                if not str(raw_item.get(key, "")).strip():
                    errors.append(f"{prefix}.{key} is required.")
            for key in REQUIRED_ITEM_LIST_FIELDS:
                value = raw_item.get(key)
                if not isinstance(value, list) or not as_list(value):
                    errors.append(f"{prefix}.{key} must be a non-empty list.")
            attachments = raw_item.get("attachments")
            if attachments is not None:
                if not isinstance(attachments, list):
                    errors.append(f"{prefix}.attachments must be a list.")
                    continue
                for attachment_index, attachment in enumerate(attachments, start=1):
                    attachment_prefix = f"{prefix}.attachments[{attachment_index}]"
                    if not isinstance(attachment, dict):
                        errors.append(f"{attachment_prefix} must be an object.")
                        continue
                    for key in ("source_path", "caption", "role", "provenance"):
                        if not str(attachment.get(key, "")).strip():
                            errors.append(f"{attachment_prefix}.{key} is required.")
                    role = str(attachment.get("role", "")).strip()
                    if role and role not in ALLOWED_ATTACHMENT_ROLES:
                        errors.append(f"{attachment_prefix}.role must be prompt or solution.")
                    provenance = str(attachment.get("provenance", "")).strip()
                    if provenance and provenance not in ALLOWED_ATTACHMENT_PROVENANCE:
                        errors.append(
                            f"{attachment_prefix}.provenance must be provided or reconstructed."
                        )
                    source_path = str(attachment.get("source_path", "")).strip()
                    if source_path:
                        try:
                            verify_source_image(source_path)
                        except ValueError as exc:
                            errors.append(f"{attachment_prefix}.source_path: {exc}")

    if errors:
        raise ValueError("Invalid question JSON:\n- " + "\n- ".join(errors))


def validate_export_date(raw_date: str) -> str:
    value = raw_date.strip()
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"source_date/date must be YYYY-MM-DD: {value}") from exc
    return value


def item_hash(course: str, item: dict[str, Any], salt: int = 0) -> str:
    title = str(item.get("title", "")).strip()
    question = str(item.get("original_question", "")).strip()
    raw = f"{course}\n{title}\n{question}"
    if salt:
        raw += f"\n#{salt}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def format_list(value: Any) -> str:
    values = as_list(value)
    if not values:
        return "- Not provided"
    return "\n".join(f"- {entry}" for entry in values)


def format_text(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "Not provided"


def render_markdown(
    data: dict[str, Any],
    export_date: str,
    records: list[dict[str, str]],
) -> str:
    course = required_text(data, "course")
    collection_type = normalize_collection_type(data.get("collection_type")) or "mistake_set"
    topic = str(data.get("topic", "Mistake Notes")).strip() or "Mistake Notes"
    lines = [
        f"# {course} - {topic} - {collection_type}",
        "",
        f"- Export date: {export_date}",
        f"- Item count: {len(records)}",
        "",
    ]
    for index, record in enumerate(records, start=1):
        item = record["item"]
        title = record["title"]
        anchor = record["anchor"]
        lines.extend(
            [
                f'<a id="{anchor}"></a>',
                "",
                f"## Q{index}. {title}",
                "",
                "**Original Question**",
                "",
                format_text(item.get("original_question")),
                "",
                "**Knowledge Points**",
                "",
                format_list(item.get("knowledge_points")),
                "",
                "**Mistake / Weak Spot**",
                "",
                format_text(item.get("mistake_reason")),
                "",
                "**Correct Approach**",
                "",
                format_text(item.get("correct_approach")),
                "",
                "**Answer Points**",
                "",
                format_list(item.get("answer_points")),
                "",
                "**Review Suggestion**",
                "",
                format_text(item.get("review_suggestion")),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def make_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    course = required_text(data, "course")
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Missing required non-empty list: items")

    records: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, dict):
            raise ValueError(f"Item {index} must be an object.")
        title = str(raw_item.get("title", "")).strip() or f"Question {index}"
        fingerprint = item_hash(course, raw_item)
        records.append(
            {
                "item": raw_item,
                "title": title,
                "hash": fingerprint,
                "anchor": f"q{index}-{fingerprint}",
            }
        )
    return records


def choose_output_path(course_dir: Path, filename: str, content: str) -> Path:
    candidate = course_dir / filename
    if not candidate.exists() or candidate.read_text(encoding="utf-8") == content:
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    for number in range(2, 1000):
        numbered = candidate.with_name(f"{stem}-{number}{suffix}")
        if not numbered.exists() or numbered.read_text(encoding="utf-8") == content:
            return numbered
    raise RuntimeError(f"Could not choose a unique output path for {candidate}")


def database_expected_but_missing(context: StorageContext) -> bool:
    return not context.db_path.exists() and any(
        (context.notes_root / name).exists() for name in SKILL_OWNED_DIRECTORIES
    )


def connect_db(context: StorageContext, *, create: bool = False) -> sqlite3.Connection:
    if context.db_path.exists():
        connection = sqlite3.connect(context.db_path)
    elif database_expected_but_missing(context):
        raise StorageMissingError(
            f"NOTES_DB_MISSING: {context.notes_root} contains exercise-bank files but no database "
            f"at {context.db_path}. It may still be syncing or may have been moved. Ask the user "
            "to restore it before saving or reviewing. Do not create a replacement database."
        )
    elif create:
        context.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(context.db_path)
    else:
        connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        ensure_schema(connection)
    except Exception:
        connection.close()
        raise
    return connection


@contextmanager
def open_db(context: StorageContext, *, create: bool = False) -> Iterator[sqlite3.Connection]:
    connection = connect_db(context, create=create)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def ensure_schema(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise CliError(
            f"This exercise bank was created by a newer version of the skill (schema {version}; "
            f"this copy supports {SCHEMA_VERSION}). Update the skill before using this database."
        )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS questions (
            id TEXT PRIMARY KEY,
            course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
            collection_type TEXT NOT NULL,
            topic TEXT NOT NULL,
            title TEXT NOT NULL,
            original_question TEXT NOT NULL,
            knowledge_points_json TEXT NOT NULL,
            mistake_reason TEXT NOT NULL,
            correct_approach TEXT NOT NULL,
            answer_points_json TEXT NOT NULL,
            review_suggestion TEXT NOT NULL,
            source_date TEXT NOT NULL,
            search_text TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'pending',
            reviewed_at TEXT,
            review_count INTEGER NOT NULL DEFAULT 0,
            wrong_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_questions_course_status_date
            ON questions(course_id, review_status, source_date, created_at);
        CREATE INDEX IF NOT EXISTS idx_questions_status_date
            ON questions(review_status, source_date, created_at);
        CREATE INDEX IF NOT EXISTS idx_questions_topic
            ON questions(topic);

        CREATE TABLE IF NOT EXISTS review_events (
            id INTEGER PRIMARY KEY,
            question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
            result TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS question_attachments (
            id INTEGER PRIMARY KEY,
            question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('prompt', 'solution')),
            provenance TEXT NOT NULL CHECK (provenance IN ('provided', 'reconstructed')),
            stored_relative_path TEXT NOT NULL UNIQUE,
            original_filename TEXT NOT NULL,
            caption TEXT NOT NULL CHECK (length(trim(caption)) > 0),
            sha256 TEXT NOT NULL,
            media_type TEXT NOT NULL CHECK (media_type IN ('image/png', 'image/jpeg', 'image/webp')),
            byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
            created_at TEXT NOT NULL,
            UNIQUE(question_id, sha256)
        );

        CREATE INDEX IF NOT EXISTS idx_question_attachments_question_role
            ON question_attachments(question_id, role, id);
        """
    )
    if version < SCHEMA_VERSION:
        migrate_schema(connection)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.commit()


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def migrate_schema(connection: sqlite3.Connection) -> None:
    if "note" not in table_columns(connection, "review_events"):
        connection.execute("ALTER TABLE review_events ADD COLUMN note TEXT")
    question_columns = table_columns(connection, "questions")
    if "review_stage" not in question_columns:
        connection.execute(
            "ALTER TABLE questions ADD COLUMN review_stage INTEGER NOT NULL DEFAULT 0"
        )
    if "next_review_at" not in question_columns:
        connection.execute("ALTER TABLE questions ADD COLUMN next_review_at TEXT")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_questions_status_next_review "
        "ON questions(review_status, next_review_at)"
    )


def get_or_create_course(connection: sqlite3.Connection, course: str) -> int:
    timestamp = now_iso()
    connection.execute(
        "INSERT OR IGNORE INTO courses(name, created_at) VALUES (?, ?)",
        (course, timestamp),
    )
    row = connection.execute("SELECT id FROM courses WHERE name = ?", (course,)).fetchone()
    if row is None:
        raise RuntimeError(f"Could not create course: {course}")
    return int(row["id"])


def delete_empty_courses(connection: sqlite3.Connection) -> None:
    connection.execute(
        "DELETE FROM courses WHERE NOT EXISTS "
        "(SELECT 1 FROM questions WHERE questions.course_id = courses.id)"
    )


def linked_directory(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def unsafe_managed_path(path: Path) -> ValueError:
    return ValueError(
        f"Unsafe managed attachment path: {path}. Run db attachment-audit before continuing."
    )


def ensure_safe_managed_directory(
    context: StorageContext,
    relative_parts: tuple[str, ...] = (),
    *,
    create: bool = False,
) -> Path:
    if create:
        context.notes_root.mkdir(parents=True, exist_ok=True)
    if context.notes_root.exists() and not context.notes_root.is_dir():
        raise unsafe_managed_path(context.notes_root)
    current = context.notes_root
    for index, part in enumerate(("attachments", *relative_parts)):
        current = current / part
        if linked_directory(current):
            raise unsafe_managed_path(current)
        if current.exists():
            if not current.is_dir():
                raise unsafe_managed_path(current)
        elif create:
            current.mkdir()
            if linked_directory(current) or not current.is_dir():
                raise unsafe_managed_path(current)
        else:
            remaining = ("attachments", *relative_parts)[index + 1 :]
            return current.joinpath(*remaining)
    return current


def managed_attachment_path(
    context: StorageContext,
    relative: str,
    *,
    create_parent: bool = False,
) -> Path:
    relative_path = Path(relative)
    parts = relative_path.parts
    if (
        relative_path.is_absolute()
        or ".." in parts
        or len(parts) < 3
        or parts[0] != "attachments"
    ):
        raise unsafe_managed_path(context.notes_root / relative_path)
    parent = ensure_safe_managed_directory(
        context, tuple(parts[1:-1]), create=create_parent
    )
    managed_path = parent / parts[-1]
    if linked_directory(managed_path) or (managed_path.exists() and not managed_path.is_file()):
        raise unsafe_managed_path(managed_path)
    return managed_path


def attachment_destination(
    context: StorageContext,
    question_id: str,
    attachment: dict[str, Any],
) -> tuple[Path, str]:
    question_component = Path(question_id)
    if (
        question_component.is_absolute()
        or len(question_component.parts) != 1
        or question_component.parts[0] in {".", ".."}
    ):
        raise unsafe_managed_path(context.attachments_root / question_component)
    stem = clean_filename(attachment["source"].stem, "image", 60)
    relative_path = (
        Path("attachments")
        / question_id
        / f"{stem}-{attachment['sha256']}{attachment['extension']}"
    )
    return (
        managed_attachment_path(context, relative_path.as_posix(), create_parent=True),
        relative_path.as_posix(),
    )


def create_verified_snapshot(
    context: StorageContext,
    question_id: str,
    raw_attachment: dict[str, Any],
) -> tuple[dict[str, Any], Path]:
    attachment = validated_attachment_request(raw_attachment)
    question_component = Path(question_id)
    if (
        question_component.is_absolute()
        or len(question_component.parts) != 1
        or question_component.parts[0] in {".", ".."}
    ):
        raise unsafe_managed_path(context.attachments_root / question_component)
    question_dir = ensure_safe_managed_directory(context, (question_id,), create=True)
    temporary = question_dir / f".incoming-{uuid.uuid4().hex}.tmp"
    try:
        digest = hashlib.sha256()
        byte_size = 0
        with attachment["source"].open("rb") as source, temporary.open("xb") as destination:
            while True:
                chunk = source.read(HASH_CHUNK_SIZE)
                if not chunk:
                    break
                byte_size += len(chunk)
                if byte_size > MAX_ATTACHMENT_BYTES:
                    raise ValueError(
                        f"Attachment exceeds {MAX_ATTACHMENT_BYTES} bytes: {attachment['source']}"
                    )
                destination.write(chunk)
                digest.update(chunk)
        media_type, extension = verified_image_type(temporary)
        return (
            {
                **attachment,
                "sha256": digest.hexdigest(),
                "byte_size": byte_size,
                "media_type": media_type,
                "extension": extension,
            },
            temporary,
        )
    except Exception:
        if temporary.exists():
            temporary.unlink()
        try:
            question_dir.rmdir()
        except OSError:
            pass
        raise


def file_matches_attachment(path: Path, attachment: dict[str, Any]) -> bool:
    if not path.exists() or not path.is_file():
        return False
    current_size = path.stat().st_size
    if current_size != int(attachment["byte_size"]) or current_size > MAX_ATTACHMENT_BYTES:
        return False
    digest, _ = file_digest_and_size(path, enforce_limit=True)
    return digest == attachment["sha256"]


def install_verified_snapshot(
    context: StorageContext,
    question_id: str,
    attachment: dict[str, Any],
    temporary: Path,
    created_files: list[Path],
) -> str:
    final_path, relative_path = attachment_destination(context, question_id, attachment)
    if final_path.exists():
        if not file_matches_attachment(final_path, attachment):
            raise ValueError(
                f"Existing managed attachment differs from verified source digest: {final_path}"
            )
        return relative_path
    temporary.replace(final_path)
    created_files.append(final_path)
    return relative_path


def cleanup_created_attachment_files(created_files: list[Path], context: StorageContext) -> None:
    for path in reversed(created_files):
        try:
            relative = path.relative_to(context.notes_root).as_posix()
            safe_path = managed_attachment_path(context, relative)
        except ValueError:
            continue
        if safe_path.exists():
            safe_path.unlink()
        parent = safe_path.parent
        while parent != context.attachments_root.parent and parent.exists():
            if linked_directory(parent):
                break
            try:
                parent.rmdir()
            except OSError:
                break
            if parent == context.attachments_root:
                break
            parent = parent.parent


def add_attachment_row(
    connection: sqlite3.Connection,
    context: StorageContext,
    question_id: str,
    raw_attachment: dict[str, Any],
    created_files: list[Path],
) -> sqlite3.Row:
    attachment, temporary = create_verified_snapshot(context, question_id, raw_attachment)
    try:
        existing = connection.execute(
            "SELECT * FROM question_attachments WHERE question_id = ? AND sha256 = ?",
            (question_id, attachment["sha256"]),
        ).fetchone()
        if existing is not None:
            if (
                existing["role"] != attachment["role"]
                or existing["provenance"] != attachment["provenance"]
                or existing["caption"] != attachment["caption"]
            ):
                raise ValueError(
                    "Attachment bytes are already linked with different metadata; "
                    "use db attachment-update after explicit user confirmation."
                )
            return existing
        relative_path = install_verified_snapshot(
            context, question_id, attachment, temporary, created_files
        )
        connection.execute(
            """
            INSERT INTO question_attachments (
                question_id, role, provenance, stored_relative_path, original_filename,
                caption, sha256, media_type, byte_size, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question_id,
                attachment["role"],
                attachment["provenance"],
                relative_path,
                attachment["original_filename"],
                attachment["caption"],
                attachment["sha256"],
                attachment["media_type"],
                attachment["byte_size"],
                now_iso(),
            ),
        )
        return connection.execute(
            "SELECT * FROM question_attachments WHERE question_id = ? AND sha256 = ?",
            (question_id, attachment["sha256"]),
        ).fetchone()
    finally:
        if temporary.exists():
            temporary.unlink()


def attachment_metadata(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "role": row["role"],
        "provenance": row["provenance"],
        "caption": row["caption"],
        "stored_relative_path": row["stored_relative_path"],
        "media_type": row["media_type"],
        "byte_size": row["byte_size"],
        "sha256": row["sha256"],
    }


def serialize_attachment(row: sqlite3.Row, context: StorageContext) -> dict[str, Any]:
    managed_path = managed_attachment_path(context, row["stored_relative_path"])
    return {
        **attachment_metadata(row),
        "managed_path": str(managed_path),
        "missing": not managed_path.exists(),
    }


def serialize_attachment_for_cleanup(
    row: sqlite3.Row, context: StorageContext
) -> tuple[dict[str, Any], Path | None]:
    try:
        attachment = serialize_attachment(row, context)
        return attachment, Path(attachment["managed_path"])
    except ValueError as exc:
        return {
            **attachment_metadata(row),
            "unsafe_path": True,
            "path_error": str(exc),
        }, None


def load_attachments(
    connection: sqlite3.Connection,
    context: StorageContext,
    question_ids: list[str],
    role: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    if not question_ids:
        return {}
    placeholders = ", ".join("?" for _ in question_ids)
    params: list[Any] = list(question_ids)
    role_sql = ""
    if role:
        role_sql = " AND role = ?"
        params.append(role)
    rows = connection.execute(
        f"""
        SELECT *
        FROM question_attachments
        WHERE question_id IN ({placeholders}){role_sql}
        ORDER BY question_id, role, id
        """,
        params,
    ).fetchall()
    attachments: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        attachments.setdefault(row["question_id"], []).append(serialize_attachment(row, context))
    return attachments


def add_attachments_to_items(
    items: list[dict[str, Any]],
    attachment_map: dict[str, list[dict[str, Any]]],
) -> None:
    for item in items:
        attachments = attachment_map.get(item["id"], [])
        if attachments:
            item["attachments"] = attachments


def verify_managed_attachment(attachment: dict[str, Any]) -> None:
    managed_path = Path(attachment["managed_path"])
    if not managed_path.exists():
        raise ValueError(f"Managed attachment is missing: {managed_path}")
    byte_size = managed_path.stat().st_size
    if byte_size != int(attachment["byte_size"]):
        raise ValueError(f"Managed attachment size differs from stored size: {managed_path}")
    if byte_size > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"Managed attachment exceeds {MAX_ATTACHMENT_BYTES} bytes: {managed_path}")
    digest, _ = file_digest_and_size(managed_path, enforce_limit=True)
    if digest != attachment["sha256"]:
        raise ValueError(f"Managed attachment differs from stored digest: {managed_path}")


def stage_managed_files(
    context: StorageContext,
    paths: list[Path],
) -> tuple[Path | None, list[tuple[Path, Path]]]:
    existing_paths: list[Path] = []
    for path in paths:
        relative = path.relative_to(context.notes_root).as_posix()
        safe_path = managed_attachment_path(context, relative)
        if safe_path.exists():
            existing_paths.append(safe_path)
    if not existing_paths:
        return None, []
    operation_id = uuid.uuid4().hex
    trash_root = ensure_safe_managed_directory(context, (".trash", operation_id), create=True)
    moves: list[tuple[Path, Path]] = []
    try:
        for source in existing_paths:
            destination_relative = (
                Path("attachments") / ".trash" / operation_id / source.relative_to(context.attachments_root)
            )
            destination = managed_attachment_path(
                context, destination_relative.as_posix(), create_parent=True
            )
            source.replace(destination)
            moves.append((source, destination))
    except OSError:
        restore_staged_files(context, moves)
        if trash_root.exists():
            shutil.rmtree(trash_root, ignore_errors=True)
        raise
    return trash_root, moves


def restore_staged_files(context: StorageContext, moves: list[tuple[Path, Path]]) -> None:
    for original, staged in reversed(moves):
        original_relative = original.relative_to(context.notes_root).as_posix()
        staged_relative = staged.relative_to(context.notes_root).as_posix()
        safe_original = managed_attachment_path(context, original_relative, create_parent=True)
        safe_staged = managed_attachment_path(context, staged_relative)
        if safe_staged.exists():
            safe_staged.replace(safe_original)


def finalize_trash_cleanup(trash_root: Path | None) -> str | None:
    if trash_root is None:
        return None
    try:
        shutil.rmtree(trash_root)
        parent = trash_root.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError as exc:
        return f"Attachment trash cleanup remains pending: {trash_root}: {exc}"
    return None


def remove_empty_question_dirs(context: StorageContext, managed_paths: list[Path]) -> None:
    for path in managed_paths:
        parent = path.parent
        if parent == context.attachments_root or not parent.exists():
            continue
        try:
            if linked_directory(parent):
                continue
            parent.rmdir()
        except OSError:
            pass


def normalize_question_for_db(
    data: dict[str, Any],
    raw_item: dict[str, Any],
    index: int,
    export_date: str,
) -> dict[str, Any]:
    question = {
        "course": required_text(data, "course"),
        "collection_type": normalize_collection_type(data.get("collection_type")) or "mistake_set",
        "topic": str(data.get("topic", "Mistake Notes")).strip() or "Mistake Notes",
        "title": str(raw_item.get("title", "")).strip() or f"Question {index}",
        "original_question": format_text(raw_item.get("original_question")),
        "knowledge_points": as_list(raw_item.get("knowledge_points")),
        "mistake_reason": format_text(raw_item.get("mistake_reason")),
        "correct_approach": format_text(raw_item.get("correct_approach")),
        "answer_points": as_list(raw_item.get("answer_points")),
        "review_suggestion": format_text(raw_item.get("review_suggestion")),
        "source_date": export_date,
    }
    question["search_text"] = build_search_text(question)
    return question


def build_search_text(question: dict[str, Any]) -> str:
    return "\n".join(
        [
            question["course"],
            question["collection_type"],
            question["topic"],
            question["title"],
            question["original_question"],
            "\n".join(question["knowledge_points"]),
            question["mistake_reason"],
            question["correct_approach"],
            "\n".join(question["answer_points"]),
            question["review_suggestion"],
        ]
    )


def question_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "course": row["course"],
        "collection_type": row["collection_type"],
        "topic": row["topic"],
        "title": row["title"],
        "original_question": row["original_question"],
        "knowledge_points": json.loads(row["knowledge_points_json"]),
        "mistake_reason": row["mistake_reason"],
        "correct_approach": row["correct_approach"],
        "answer_points": json.loads(row["answer_points_json"]),
        "review_suggestion": row["review_suggestion"],
        "source_date": row["source_date"],
    }


def today_iso() -> str:
    return date.today().isoformat()


def days_from_today(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def is_due(row: sqlite3.Row) -> bool:
    return row["review_status"] == "pending" and (
        row["next_review_at"] is None or row["next_review_at"] <= today_iso()
    )


def review_schedule_fields(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "review_stage": row["review_stage"],
        "next_review_at": row["next_review_at"],
        "due": is_due(row),
    }


def row_to_question(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        **question_from_row(row),
        "review_status": row["review_status"],
        **review_schedule_fields(row),
        "reviewed_at": row["reviewed_at"],
        "review_count": row["review_count"],
        "wrong_count": row["wrong_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_quiz_prompt(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "course": row["course"],
        "collection_type": row["collection_type"],
        "topic": row["topic"],
        "title": row["title"],
        "original_question": row["original_question"],
        "knowledge_points": json.loads(row["knowledge_points_json"]),
        "source_date": row["source_date"],
        "review_status": row["review_status"],
        **review_schedule_fields(row),
        "wrong_count": row["wrong_count"],
        "review_count": row["review_count"],
    }


def plan_review(row: sqlite3.Row, result: str) -> dict[str, Any]:
    stage = int(row["review_stage"])
    if result == "wrong":
        return {"review_status": "pending", "review_stage": 0, "next_review_at": days_from_today(1), "advanced": False}
    if result == "partial":
        return {"review_status": "pending", "review_stage": stage, "next_review_at": days_from_today(1), "advanced": False}
    if not is_due(row):
        return {
            "review_status": row["review_status"],
            "review_stage": stage,
            "next_review_at": row["next_review_at"],
            "advanced": False,
        }
    if stage >= len(REVIEW_INTERVAL_DAYS):
        return {"review_status": "done", "review_stage": stage, "next_review_at": None, "advanced": True}
    return {
        "review_status": "pending",
        "review_stage": stage + 1,
        "next_review_at": days_from_today(REVIEW_INTERVAL_DAYS[stage]),
        "advanced": True,
    }


def record_review_event(
    connection: sqlite3.Connection,
    question_id: str,
    result: str,
    note: str | None,
    timestamp: str,
) -> None:
    connection.execute(
        "INSERT INTO review_events(question_id, result, note, created_at) VALUES (?, ?, ?, ?)",
        (question_id, result, note, timestamp),
    )


def apply_review_result(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    result: str,
    note: str | None,
    timestamp: str,
) -> dict[str, Any]:
    plan = plan_review(row, result)
    wrong = 1 if result == "wrong" else 0
    connection.execute(
        """
        UPDATE questions
        SET review_status = ?, review_stage = ?, next_review_at = ?, reviewed_at = ?,
            review_count = review_count + ?, wrong_count = wrong_count + ?, updated_at = ?
        WHERE id = ?
        """,
        (
            plan["review_status"],
            plan["review_stage"],
            plan["next_review_at"],
            timestamp,
            1 - wrong,
            wrong,
            timestamp,
            row["id"],
        ),
    )
    record_review_event(connection, row["id"], result, note, timestamp)
    return {
        **plan,
        "previous_stage": int(row["review_stage"]),
        "mastered": plan["review_status"] == "done",
    }


def master_question(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    note: str | None,
    timestamp: str,
) -> dict[str, Any]:
    connection.execute(
        """
        UPDATE questions
        SET review_status = 'done', next_review_at = NULL, reviewed_at = ?,
            review_count = review_count + 1, updated_at = ?
        WHERE id = ?
        """,
        (timestamp, timestamp, row["id"]),
    )
    record_review_event(connection, row["id"], "mastered", note, timestamp)
    stage = int(row["review_stage"])
    return {
        "review_status": "done",
        "review_stage": stage,
        "next_review_at": None,
        "advanced": False,
        "previous_stage": stage,
        "mastered": True,
    }


def reset_review_state(
    connection: sqlite3.Connection,
    question_id: str,
    event_result: str,
    note: str | None,
    timestamp: str,
) -> None:
    connection.execute(
        """
        UPDATE questions
        SET review_status = 'pending', review_stage = 0, next_review_at = NULL, updated_at = ?
        WHERE id = ?
        """,
        (timestamp, question_id),
    )
    record_review_event(connection, question_id, event_result, note, timestamp)


def render_question_content(item: dict[str, Any]) -> str:
    lines = [
        f"Course: {item['course']}",
        f"Topic: {item['topic']}",
        f"Title: {item['title']}",
        "",
        "Original question:",
        item["original_question"],
    ]
    prompt_attachments = [
        attachment
        for attachment in item.get("attachments", [])
        if attachment["role"] == "prompt"
    ]
    solution_attachments = [
        attachment
        for attachment in item.get("attachments", [])
        if attachment["role"] == "solution"
    ]
    if prompt_attachments:
        lines.extend(["", "Prompt images:"])
        lines.extend(
            f"- {attachment['caption']}: {attachment['managed_path']}"
            for attachment in prompt_attachments
        )
    lines.extend(
        [
            "",
            "Knowledge points:",
            format_list(item["knowledge_points"]),
            "",
            "Mistake / weak spot:",
            item["mistake_reason"],
            "",
            "Correct approach:",
            item["correct_approach"],
            "",
            "Answer points:",
            format_list(item["answer_points"]),
        ]
    )
    if solution_attachments:
        lines.extend(["", "Solution images:"])
        lines.extend(
            f"- {attachment['caption']}: {attachment['managed_path']}"
            for attachment in solution_attachments
        )
    lines.extend(["", "Review suggestion:", item["review_suggestion"]])
    return "\n".join(lines)


def render_markdown_attachment(attachment: dict[str, Any]) -> str:
    caption = (
        re.sub(r"\s+", " ", attachment["caption"]).strip()
        .replace("\\", r"\\")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )
    relative_path = (Path("..") / Path(attachment["stored_relative_path"])).as_posix()
    return f"![{caption}]({quote(relative_path, safe='/')})"


def render_db_question_export(
    items: list[dict[str, Any]],
    *,
    mode: str,
    course: str | None,
    topic: str | None,
) -> str:
    scope = " / ".join(filter(None, [course, topic])) or "Selected Questions"
    lines = [f"# {scope}", ""]

    for index, item in enumerate(items, start=1):
        prompt_attachments = [
            attachment
            for attachment in item.get("attachments", [])
            if attachment["role"] == "prompt"
        ]
        solution_attachments = [
            attachment
            for attachment in item.get("attachments", [])
            if attachment["role"] == "solution"
        ]
        lines.extend(
            [
                f"## {index}. {item['title']}",
                "",
                "### 问题",
                "",
                item["original_question"],
                "",
            ]
        )
        for attachment in prompt_attachments:
            lines.extend([render_markdown_attachment(attachment), ""])
        if mode == "full":
            lines.extend(
                [
                    "### 回答",
                    "",
                    format_list(item["answer_points"]),
                    "",
                ]
            )
            for attachment in solution_attachments:
                lines.extend([render_markdown_attachment(attachment), ""])
        if index != len(items):
            lines.extend(["---", ""])
    return "\n".join(lines).rstrip() + "\n"


def question_content_values(question: dict[str, Any]) -> tuple[Any, ...]:
    return (
        question["collection_type"],
        question["topic"],
        question["title"],
        question["original_question"],
        json.dumps(question["knowledge_points"], ensure_ascii=False),
        question["mistake_reason"],
        question["correct_approach"],
        json.dumps(question["answer_points"], ensure_ascii=False),
        question["review_suggestion"],
        question["source_date"],
        question["search_text"],
    )


def fetch_question(connection: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return connection.execute(f"{QUESTION_SELECT} WHERE q.id = ?", (item_id,)).fetchone()


def find_question_id(
    connection: sqlite3.Connection,
    course_id: int,
    title: str,
    original_question: str,
    exclude_id: str | None = None,
) -> str | None:
    row = connection.execute(
        """
        SELECT id FROM questions
        WHERE course_id = ? AND title = ? AND original_question = ? AND id IS NOT ?
        ORDER BY created_at ASC, id ASC
        LIMIT 1
        """,
        (course_id, title, original_question, exclude_id),
    ).fetchone()
    return None if row is None else str(row["id"])


def allocate_question_id(connection: sqlite3.Connection, course: str, raw_item: dict[str, Any]) -> str:
    for salt in range(1000):
        candidate = item_hash(course, raw_item, salt)
        taken = connection.execute("SELECT 1 FROM questions WHERE id = ?", (candidate,)).fetchone()
        if taken is None:
            return candidate
    raise RuntimeError(f"Could not allocate a unique question id for: {raw_item.get('title')}")


def insert_question(
    connection: sqlite3.Connection,
    question_id: str,
    course_id: int,
    question: dict[str, Any],
    timestamp: str,
) -> None:
    connection.execute(
        """
        INSERT INTO questions (
            id, course_id, collection_type, topic, title, original_question,
            knowledge_points_json, mistake_reason, correct_approach,
            answer_points_json, review_suggestion, source_date, search_text,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (question_id, course_id, *question_content_values(question), timestamp, timestamp),
    )


def update_question_content(
    connection: sqlite3.Connection,
    question_id: str,
    course_id: int,
    question: dict[str, Any],
    timestamp: str,
) -> None:
    connection.execute(
        """
        UPDATE questions
        SET course_id = ?, collection_type = ?, topic = ?, title = ?, original_question = ?,
            knowledge_points_json = ?, mistake_reason = ?, correct_approach = ?,
            answer_points_json = ?, review_suggestion = ?, source_date = ?, search_text = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (course_id, *question_content_values(question), timestamp, question_id),
    )


def insert_questions(
    connection: sqlite3.Connection,
    data: dict[str, Any],
    export_date: str,
    context: StorageContext | None = None,
    created_files: list[Path] | None = None,
) -> dict[str, Any]:
    course = required_text(data, "course")
    course_id = get_or_create_course(connection, course)
    timestamp = now_iso()
    result: dict[str, Any] = {"inserted": 0, "updated": 0, "ids": [], "requeued": []}
    for index, raw_item in enumerate(data["items"], start=1):
        question = normalize_question_for_db(data, raw_item, index, export_date)
        question_id = find_question_id(
            connection, course_id, question["title"], question["original_question"]
        )
        if question_id is None:
            question_id = allocate_question_id(connection, course, raw_item)
            insert_question(connection, question_id, course_id, question, timestamp)
            result["inserted"] += 1
        else:
            update_question_content(connection, question_id, course_id, question, timestamp)
            result["updated"] += 1
            if question["collection_type"] == "mistake_set":
                reset_review_state(
                    connection, question_id, "requeued", "Saved again as a mistake.", timestamp
                )
                result["requeued"].append(question_id)
        result["ids"].append(question_id)
        attachments = raw_item.get("attachments") or []
        if attachments and (context is None or created_files is None):
            raise ValueError("Attachment storage context is required when adding images.")
        for raw_attachment in attachments:
            add_attachment_row(connection, context, question_id, raw_attachment, created_files)
    return result


def like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def question_filters(
    *, query: str | None, course: str | None, topic: str | None
) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    for column, value in (("q.search_text", query), ("c.name", course), ("q.topic", topic)):
        if value:
            where.append(f"{column} LIKE ? ESCAPE '\\'")
            params.append(like_pattern(value))
    return where, params


def where_clause(conditions: list[str]) -> str:
    return "WHERE " + " AND ".join(conditions) if conditions else ""


def build_question_query(
    *,
    query: str | None,
    course: str | None,
    topic: str | None,
    review_status: str,
    limit: int,
) -> tuple[str, list[Any]]:
    where, params = question_filters(query=query, course=course, topic=topic)
    if review_status != "all":
        where.append("q.review_status = ?")
        params.append(review_status)
    sql = f"""
        {QUESTION_SELECT}
        {where_clause(where)}
        ORDER BY q.source_date ASC, q.created_at ASC, q.id ASC
        LIMIT ?
    """
    return sql, [*params, limit]


def build_review_query(
    *,
    query: str | None,
    course: str | None,
    topic: str | None,
    limit: int,
    due_only: bool,
    include_done: bool,
    shuffle: bool,
) -> tuple[str, list[Any]]:
    today = today_iso()
    where, params = question_filters(query=query, course=course, topic=topic)
    if due_only:
        where.append(DUE_CONDITION)
        params.append(today)
    elif not include_done:
        where.append("q.review_status = 'pending'")
    review_group = f"CASE WHEN {DUE_CONDITION} THEN 0 WHEN q.review_status = 'pending' THEN 1 ELSE 2 END"
    if shuffle:
        order = f"{review_group}, random()"
    else:
        order = (
            f"{review_group}, COALESCE(q.next_review_at, substr(q.created_at, 1, 10)) ASC, "
            "q.wrong_count DESC, q.source_date ASC, q.created_at ASC, q.id ASC"
        )
    sql = f"""
        {QUESTION_SELECT}
        {where_clause(where)}
        ORDER BY {order}
        LIMIT ?
    """
    return sql, [*params, today, limit]


def db_add(args: argparse.Namespace) -> int:
    if not args.confirmed_selection_by_user:
        raise ConfirmationRequiredError(
            "Adding questions requires explicit user selection. "
            "Use --confirmed-selection-by-user only after the user selects the candidate items to save."
        )
    data = read_json_arg(args.input)
    validate_review_data(data)
    export_date = validate_export_date(
        args.date or str(data.get("source_date", "")).strip() or date.today().isoformat()
    )
    context = storage_from_args(args)
    created_files: list[Path] = []
    committed = False
    item_results: list[dict[str, Any]] = []
    try:
        with open_db(context, create=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            saved = insert_questions(connection, data, export_date, context, created_files)
            connection.commit()
            committed = True
            if any(item.get("attachments") for item in data["items"]):
                unique_ids = list(dict.fromkeys(saved["ids"]))
                item_results = [row_to_question(fetch_question(connection, item_id)) for item_id in unique_ids]
                add_attachments_to_items(item_results, load_attachments(connection, context, unique_ids))
    except Exception:
        if not committed:
            cleanup_created_attachment_files(created_files, context)
        raise

    result = {"db_path": str(context.db_path), **saved}
    if item_results:
        result["items"] = item_results
    print_json(result)
    return 0


def db_validate(args: argparse.Namespace) -> int:
    data = read_json_arg(args.input)
    validate_review_data(data)
    export_date = validate_export_date(
        args.date or str(data.get("source_date", "")).strip() or date.today().isoformat()
    )
    print_json(
        {
            "valid": True,
            "course": str(data["course"]).strip(),
            "collection_type": normalize_collection_type(data.get("collection_type")),
            "topic": str(data["topic"]).strip(),
            "source_date": export_date,
            "item_count": len(data["items"]),
        }
    )
    return 0


def db_search(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise ValueError("--limit must be at least 1.")
    context = storage_from_args(args)
    with open_db(context) as connection:
        sql, params = build_question_query(
            query=args.query,
            course=args.course,
            topic=args.topic,
            review_status=args.review_status,
            limit=args.limit,
        )
        rows = connection.execute(sql, params).fetchall()
        attachment_map = load_attachments(connection, context, [row["id"] for row in rows])

    items = [row_to_question(row) for row in rows]
    add_attachments_to_items(items, attachment_map)
    if args.include_content:
        for item in items:
            item["content"] = render_question_content(item)
    print_json({"db_path": str(context.db_path), "returned_count": len(items), "items": items})
    return 0


def db_show(args: argparse.Namespace) -> int:
    context = storage_from_args(args)
    with open_db(context) as connection:
        row = fetch_question(connection, args.item_id)
        if row is None:
            raise ValueError(f"Question not found: {args.item_id}")
        attachments = load_attachments(connection, context, [args.item_id]).get(args.item_id, [])
        history = connection.execute(
            """
            SELECT result, note, created_at
            FROM review_events
            WHERE question_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (args.item_id, HISTORY_LIMIT),
        ).fetchall()

    item = row_to_question(row)
    if attachments:
        item["attachments"] = attachments
    item["content"] = render_question_content(item)
    item["history"] = [dict(event) for event in history]
    print_json({"db_path": str(context.db_path), "item": item})
    return 0


def db_attach(args: argparse.Namespace) -> int:
    load_pillow()
    context = storage_from_args(args)
    created_files: list[Path] = []
    committed = False
    try:
        with open_db(context) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if fetch_question(connection, args.item_id) is None:
                raise ValueError(f"Question not found: {args.item_id}")
            attachment = add_attachment_row(
                connection,
                context,
                args.item_id,
                {
                    "source_path": args.source,
                    "role": args.role,
                    "provenance": args.provenance,
                    "caption": args.caption,
                },
                created_files,
            )
            connection.commit()
            committed = True
    except Exception:
        if not committed:
            cleanup_created_attachment_files(created_files, context)
        raise

    print_json(
        {
            "db_path": str(context.db_path),
            "item_id": args.item_id,
            "attachment": serialize_attachment(attachment, context),
        }
    )
    return 0


def db_attachment_update(args: argparse.Namespace) -> int:
    updates: dict[str, str] = {}
    for field in ("role", "provenance", "caption"):
        value = getattr(args, field)
        if value is not None:
            normalized = str(value).strip()
            if not normalized:
                raise ValueError(f"Attachment {field} cannot be empty.")
            updates[field] = normalized
    if not updates:
        raise ValueError("At least one attachment metadata field must be updated.")
    context = storage_from_args(args)
    with open_db(context) as connection:
        row = connection.execute(
            "SELECT * FROM question_attachments WHERE id = ?", (args.attachment_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Attachment not found: {args.attachment_id}")
        if not args.confirmed_by_user:
            raise ConfirmationRequiredError(
                "Attachment metadata changes require explicit user confirmation. "
                "Use --confirmed-by-user only after the user approves the change.",
                {
                    "attachment_id": args.attachment_id,
                    "question_id": row["question_id"],
                    "changes": {
                        field: {"from": row[field], "to": value} for field, value in updates.items()
                    },
                },
            )
        assignments = ", ".join(f"{field} = ?" for field in updates)
        connection.execute(
            f"UPDATE question_attachments SET {assignments} WHERE id = ?",
            (*updates.values(), args.attachment_id),
        )
        connection.commit()
        updated = connection.execute(
            "SELECT * FROM question_attachments WHERE id = ?", (args.attachment_id,)
        ).fetchone()

    print_json({"db_path": str(context.db_path), "attachment": serialize_attachment(updated, context)})
    return 0


def db_detach(args: argparse.Namespace) -> int:
    context = storage_from_args(args)
    managed_paths: list[Path] = []
    file_cleanup_skipped: list[dict[str, Any]] = []
    with open_db(context) as connection:
        row = connection.execute(
            "SELECT * FROM question_attachments WHERE id = ?", (args.attachment_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Attachment not found: {args.attachment_id}")
        attachment, managed_path = serialize_attachment_for_cleanup(row, context)
        if not args.confirmed_by_user:
            raise ConfirmationRequiredError(
                "Detaching an attachment requires explicit user confirmation. "
                "Use --confirmed-by-user only after the user approves deletion.",
                {"question_id": row["question_id"], "attachment": attachment},
            )
        if managed_path is None:
            file_cleanup_skipped.append(attachment)
        else:
            managed_paths.append(managed_path)
        trash_root, moves = stage_managed_files(context, managed_paths)
        try:
            connection.execute("DELETE FROM question_attachments WHERE id = ?", (args.attachment_id,))
            connection.commit()
        except Exception:
            connection.rollback()
            restore_staged_files(context, moves)
            raise
    cleanup_warning = finalize_trash_cleanup(trash_root)
    remove_empty_question_dirs(context, managed_paths)

    result = {"db_path": str(context.db_path), "detached_attachment": attachment}
    if cleanup_warning:
        result["cleanup_warning"] = cleanup_warning
    if file_cleanup_skipped:
        result["file_cleanup_skipped"] = file_cleanup_skipped
    print_json(result)
    return 0


def db_attachment_audit(args: argparse.Namespace) -> int:
    if (args.prune_orphans or args.empty_trash) and not args.confirmed_by_user:
        raise ConfirmationRequiredError(
            "Attachment cleanup requires explicit user confirmation. "
            "Use --confirmed-by-user only after the user approves deletion."
        )
    context = storage_from_args(args)
    with open_db(context) as connection:
        rows = connection.execute(
            "SELECT * FROM question_attachments ORDER BY question_id, role, id"
        ).fetchall()
    referenced: set[Path] = set()
    missing_files: list[dict[str, Any]] = []
    modified_files: list[dict[str, Any]] = []
    unsafe_paths: list[dict[str, Any]] = []
    for row in rows:
        try:
            attachment = serialize_attachment(row, context)
        except ValueError as exc:
            unsafe_paths.append({**attachment_metadata(row), "path_error": str(exc)})
            continue
        path = Path(attachment["managed_path"])
        referenced.add(path)
        if attachment["missing"]:
            missing_files.append(attachment)
        else:
            try:
                verify_managed_attachment(attachment)
            except ValueError:
                modified_files.append(attachment)
    orphan_paths: list[Path] = []
    trash_paths: list[Path] = []
    try:
        attachments_root = ensure_safe_managed_directory(context)
    except ValueError as exc:
        unsafe_paths.append({"stored_relative_path": "attachments", "path_error": str(exc)})
        attachments_root = None
    if attachments_root is not None and attachments_root.exists():
        for current_root, directory_names, file_names in os.walk(
            attachments_root, topdown=True, followlinks=False
        ):
            current = Path(current_root)
            for name in list(directory_names):
                directory = current / name
                if linked_directory(directory):
                    unsafe_paths.append(
                        {
                            "stored_relative_path": directory.relative_to(context.notes_root).as_posix(),
                            "path_error": str(unsafe_managed_path(directory)),
                        }
                    )
                    directory_names.remove(name)
            for name in file_names:
                path = current / name
                if linked_directory(path) or not path.is_file():
                    unsafe_paths.append(
                        {
                            "stored_relative_path": path.relative_to(context.notes_root).as_posix(),
                            "path_error": str(unsafe_managed_path(path)),
                        }
                    )
                    continue
                relative_parts = path.relative_to(attachments_root).parts
                if relative_parts and relative_parts[0] == ".trash":
                    trash_paths.append(path)
                elif path not in referenced:
                    orphan_paths.append(path)
    if args.prune_orphans:
        for path in orphan_paths:
            relative = path.relative_to(context.notes_root).as_posix()
            managed_attachment_path(context, relative).unlink()
    if args.empty_trash and attachments_root is not None:
        trash_root = context.attachments_root / ".trash"
        unsafe_trash = any(
            str(entry.get("stored_relative_path", "")).startswith("attachments/.trash")
            for entry in unsafe_paths
        )
        if trash_root.exists() and not unsafe_trash:
            ensure_safe_managed_directory(context, (".trash",))
            shutil.rmtree(trash_root)

    print_json(
        {
            "db_path": str(context.db_path),
            "missing_files": missing_files,
            "modified_files": modified_files,
            "orphan_files": [str(path) for path in orphan_paths],
            "trash_files": [str(path) for path in trash_paths],
            "unsafe_paths": unsafe_paths,
        }
    )
    return 0


def db_pending(args: argparse.Namespace) -> int:
    args.review_status = "pending"
    args.query = args.query or None
    return db_search(args)


def exact_scope_filters(course: str | None, topic: str | None) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if course:
        where.append("c.name = ?")
        params.append(course)
    if topic:
        where.append("q.topic = ?")
        params.append(topic)
    return where, params


def db_export(args: argparse.Namespace) -> int:
    course = str(args.course or "").strip() or None
    topic = str(args.topic or "").strip() or None
    if not course and not topic:
        raise ValueError("At least one of --course or --topic is required for export.")
    export_date = validate_export_date(args.date or date.today().isoformat())
    context = storage_from_args(args)
    where, params = exact_scope_filters(course, topic)
    with open_db(context) as connection:
        rows = connection.execute(
            f"""
            {QUESTION_SELECT}
            {where_clause(where)}
            ORDER BY q.source_date ASC, q.created_at ASC, q.id ASC
            """,
            params,
        ).fetchall()
        attachment_map = load_attachments(connection, context, [row["id"] for row in rows])
    if not rows:
        raise ValueError("No questions matched the export filters.")
    items = [row_to_question(row) for row in rows]
    add_attachments_to_items(items, attachment_map)
    for item in items:
        for attachment in item.get("attachments", []):
            if args.mode == "full" or attachment["role"] == "prompt":
                verify_managed_attachment(attachment)
    markdown_content = render_db_question_export(items, mode=args.mode, course=course, topic=topic)
    scope_label = "-".join(clean_filename(part, "questions") for part in (course, topic) if part)
    export_dir = context.notes_root / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{export_date}-{scope_label}-{args.mode}.md"
    markdown_path = choose_output_path(export_dir, filename, markdown_content)
    if not markdown_path.exists() or markdown_path.read_text(encoding="utf-8") != markdown_content:
        markdown_path.write_text(markdown_content, encoding="utf-8")

    print_json(
        {
            "db_path": str(context.db_path),
            "markdown_path": str(markdown_path),
            "item_count": len(items),
            "mode": args.mode,
            "course": course,
            "topic": topic,
        }
    )
    return 0


def list_review_prompts(args: argparse.Namespace, *, due_only: bool) -> int:
    if args.limit < 1:
        raise ValueError("--limit must be at least 1.")
    context = storage_from_args(args)
    with open_db(context) as connection:
        sql, params = build_review_query(
            query=args.query,
            course=args.course,
            topic=args.topic,
            limit=args.limit,
            due_only=due_only,
            include_done=getattr(args, "include_done", False),
            shuffle=args.shuffle,
        )
        rows = connection.execute(sql, params).fetchall()
        attachment_map = load_attachments(
            connection, context, [row["id"] for row in rows], role="prompt"
        )

    items = [row_to_quiz_prompt(row) for row in rows]
    add_attachments_to_items(items, attachment_map)
    print_json(
        {
            "db_path": str(context.db_path),
            "mode": args.db_command,
            "returned_count": len(items),
            "items": items,
        }
    )
    return 0


def db_quiz(args: argparse.Namespace) -> int:
    return list_review_prompts(args, due_only=False)


def db_due(args: argparse.Namespace) -> int:
    return list_review_prompts(args, due_only=True)


def record_review(args: argparse.Namespace, result: str) -> int:
    note = str(getattr(args, "note", "") or "").strip() or None
    context = storage_from_args(args)
    timestamp = now_iso()
    with open_db(context) as connection:
        row = fetch_question(connection, args.item_id)
        if row is None:
            raise ValueError(f"Question not found: {args.item_id}")
        if result == "mastered":
            schedule = master_question(connection, row, note, timestamp)
        else:
            schedule = apply_review_result(connection, row, result, note, timestamp)
        connection.commit()
        updated_row = fetch_question(connection, args.item_id)

    print_json(
        {
            "db_path": str(context.db_path),
            "item": row_to_question(updated_row),
            "event": {"result": result, "note": note, "created_at": timestamp},
            "schedule": schedule,
        }
    )
    return 0


def db_grade(args: argparse.Namespace) -> int:
    return record_review(args, args.result)


def db_mark_done(args: argparse.Namespace) -> int:
    return record_review(args, "mastered")


def db_mark_wrong(args: argparse.Namespace) -> int:
    return record_review(args, "wrong")


def db_reset(args: argparse.Namespace) -> int:
    course = str(args.course or "").strip() or None
    topic = str(args.topic or "").strip() or None
    item_ids = [item_id.strip() for item_id in args.item_ids if item_id.strip()]
    if not course and not topic and not item_ids:
        raise ValueError("Reset requires --course, --topic, or at least one item id.")
    context = storage_from_args(args)
    where, params = exact_scope_filters(course, topic)
    if item_ids:
        where.append(f"q.id IN ({', '.join('?' for _ in item_ids)})")
        params.extend(item_ids)
    with open_db(context) as connection:
        rows = connection.execute(
            f"""
            {QUESTION_SELECT}
            {where_clause(where)}
            ORDER BY q.source_date ASC, q.created_at ASC, q.id ASC
            """,
            params,
        ).fetchall()
        if not rows:
            raise ValueError("No questions matched the reset scope.")
        if not args.confirmed_by_user:
            raise ConfirmationRequiredError(
                "Resetting review progress requires explicit user confirmation. "
                "Use --confirmed-by-user only after the user approves the reset.",
                {
                    "course": course,
                    "topic": topic,
                    "item_count": len(rows),
                    "items": [
                        {
                            "id": row["id"],
                            "title": row["title"],
                            "review_status": row["review_status"],
                            "review_stage": row["review_stage"],
                            "next_review_at": row["next_review_at"],
                        }
                        for row in rows
                    ],
                },
            )
        timestamp = now_iso()
        for row in rows:
            reset_review_state(connection, row["id"], "reset", None, timestamp)
        connection.commit()

    print_json(
        {
            "db_path": str(context.db_path),
            "reset_count": len(rows),
            "ids": [row["id"] for row in rows],
        }
    )
    return 0


def db_delete(args: argparse.Namespace) -> int:
    context = storage_from_args(args)
    managed_paths: list[Path] = []
    file_cleanup_skipped: list[dict[str, Any]] = []
    with open_db(context) as connection:
        row = fetch_question(connection, args.item_id)
        if row is None:
            raise ValueError(f"Question not found: {args.item_id}")
        deleted_item = row_to_question(row)
        attachment_rows = connection.execute(
            "SELECT * FROM question_attachments WHERE question_id = ? ORDER BY id",
            (args.item_id,),
        ).fetchall()
        attachments: list[dict[str, Any]] = []
        for attachment_row in attachment_rows:
            attachment, managed_path = serialize_attachment_for_cleanup(attachment_row, context)
            attachments.append(attachment)
            if managed_path is None:
                file_cleanup_skipped.append(attachment)
            else:
                managed_paths.append(managed_path)
        if not args.confirmed_by_user:
            raise ConfirmationRequiredError(
                "Deleting a question requires explicit user confirmation. "
                "Use --confirmed-by-user only after the user approves permanent deletion.",
                {
                    "id": deleted_item["id"],
                    "course": deleted_item["course"],
                    "topic": deleted_item["topic"],
                    "title": deleted_item["title"],
                    "original_question": deleted_item["original_question"],
                    "review_status": deleted_item["review_status"],
                    "attachment_count": len(attachments),
                },
            )
        if attachments:
            deleted_item["attachments"] = attachments
        trash_root, moves = stage_managed_files(context, managed_paths)
        try:
            connection.execute("DELETE FROM questions WHERE id = ?", (args.item_id,))
            delete_empty_courses(connection)
            connection.commit()
        except Exception:
            connection.rollback()
            restore_staged_files(context, moves)
            raise
    cleanup_warning = finalize_trash_cleanup(trash_root)
    remove_empty_question_dirs(context, managed_paths)

    result = {"db_path": str(context.db_path), "deleted_item": deleted_item}
    if cleanup_warning:
        result["cleanup_warning"] = cleanup_warning
    if file_cleanup_skipped:
        result["file_cleanup_skipped"] = file_cleanup_skipped
    print_json(result)
    return 0


def normalize_item_changes(raw_changes: dict[str, Any]) -> dict[str, Any]:
    editable = (*EDITABLE_TEXT_FIELDS, *EDITABLE_LIST_FIELDS)
    unknown = sorted(set(raw_changes) - set(editable))
    if unknown:
        raise ValueError(
            f"Unsupported update fields: {', '.join(unknown)}. Editable fields: {', '.join(editable)}."
        )
    if not raw_changes:
        raise ValueError("Update JSON must contain at least one editable field.")
    changes: dict[str, Any] = {}
    for field, value in raw_changes.items():
        if field in EDITABLE_LIST_FIELDS:
            if not isinstance(value, list) or not as_list(value):
                raise ValueError(f"{field} must be a non-empty list.")
            changes[field] = as_list(value)
        elif field == "collection_type":
            collection_type = normalize_collection_type(value)
            if not collection_type:
                allowed = " / ".join(sorted(ALLOWED_COLLECTION_TYPES))
                raise ValueError(f"collection_type must be one of: {allowed}.")
            changes[field] = collection_type
        else:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string.")
            changes[field] = value.strip()
    return changes


def db_update(args: argparse.Namespace) -> int:
    changes = normalize_item_changes(read_json_arg(args.input))
    context = storage_from_args(args)
    with open_db(context) as connection:
        row = fetch_question(connection, args.item_id)
        if row is None:
            raise ValueError(f"Question not found: {args.item_id}")
        current = question_from_row(row)
        updated = {**current, **changes}
        diff = {
            field: {"from": current[field], "to": updated[field]}
            for field in changes
            if current[field] != updated[field]
        }
        if not diff:
            raise ValueError("The update does not change any field.")
        if not args.confirmed_by_user:
            raise ConfirmationRequiredError(
                "Updating a saved question requires explicit user confirmation. "
                "Use --confirmed-by-user only after the user approves the exact changes.",
                {"id": args.item_id, "changes": diff},
            )
        course_id = get_or_create_course(connection, updated["course"])
        duplicate = find_question_id(
            connection, course_id, updated["title"], updated["original_question"], args.item_id
        )
        if duplicate is not None:
            raise ValueError(
                "A question with the same course, title, and original question already exists: "
                f"{duplicate}"
            )
        updated["search_text"] = build_search_text(updated)
        update_question_content(connection, args.item_id, course_id, updated, now_iso())
        delete_empty_courses(connection)
        connection.commit()
        updated_row = fetch_question(connection, args.item_id)

    print_json({"db_path": str(context.db_path), "item": row_to_question(updated_row), "changes": diff})
    return 0


def db_rename_course(args: argparse.Namespace) -> int:
    old_course = str(args.old_course).strip()
    new_course = str(args.new_course).strip()
    if not old_course or not new_course:
        raise ValueError("Both old and new course names are required.")
    if old_course == new_course:
        raise ValueError("Old and new course names must be different.")
    context = storage_from_args(args)
    with open_db(context) as connection:
        delete_empty_courses(connection)
        source = connection.execute("SELECT id FROM courses WHERE name = ?", (old_course,)).fetchone()
        if source is None:
            raise ValueError(f"Course not found: {old_course}")
        target = connection.execute("SELECT id FROM courses WHERE name = ?", (new_course,)).fetchone()
        if target is not None:
            raise ValueError(f"Target course already exists: {new_course}")
        course_id = int(source["id"])
        rows = connection.execute(f"{QUESTION_SELECT} WHERE q.course_id = ?", (course_id,)).fetchall()
        if not args.confirmed_by_user:
            raise ConfirmationRequiredError(
                "Course rename requires explicit user confirmation. "
                "Use --confirmed-by-user only after the user approves the exact rename.",
                {"old_course": old_course, "new_course": new_course, "question_count": len(rows)},
            )
        timestamp = now_iso()
        connection.execute("UPDATE courses SET name = ? WHERE id = ?", (new_course, course_id))
        for row in rows:
            renamed = {**question_from_row(row), "course": new_course}
            connection.execute(
                "UPDATE questions SET search_text = ?, updated_at = ? WHERE id = ?",
                (build_search_text(renamed), timestamp, row["id"]),
            )
        connection.commit()

    print_json(
        {
            "db_path": str(context.db_path),
            "old_course": old_course,
            "new_course": new_course,
            "renamed_questions": len(rows),
        }
    )
    return 0


def count_summary(counts: dict[str, Any]) -> str:
    return f"({counts['due']} due / {counts['pending']} pending / {counts['total']} total)"


def db_stats(args: argparse.Namespace) -> int:
    context = storage_from_args(args)
    counts = f"""
        COUNT(*) AS total,
        SUM(CASE WHEN q.review_status = 'pending' THEN 1 ELSE 0 END) AS pending,
        SUM(CASE WHEN q.review_status = 'done' THEN 1 ELSE 0 END) AS done,
        SUM(CASE WHEN {DUE_CONDITION} THEN 1 ELSE 0 END) AS due
    """
    today = today_iso()
    with open_db(context) as connection:
        courses = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT c.name AS course, {counts}
                FROM questions q
                JOIN courses c ON c.id = q.course_id
                GROUP BY c.name
                ORDER BY c.name
                """,
                (today,),
            )
        ]
        if args.topics:
            topics_by_course: dict[str, list[dict[str, Any]]] = {}
            for row in connection.execute(
                f"""
                SELECT c.name AS course, q.topic AS topic, {counts}
                FROM questions q
                JOIN courses c ON c.id = q.course_id
                GROUP BY c.name, q.topic
                ORDER BY c.name, q.topic
                """,
                (today,),
            ):
                topic = dict(row)
                topics_by_course.setdefault(topic.pop("course"), []).append(topic)
            for course in courses:
                course["topics"] = topics_by_course.get(course["course"], [])

    if not args.human:
        print_json({"db_path": str(context.db_path), "courses": courses})
        return 0
    if not courses:
        print("No courses are recorded in the exercise bank yet.")
        return 0
    lines = ["Available courses:"]
    for index, course in enumerate(courses, start=1):
        lines.append(f"{index}. {course['course']} {count_summary(course)}")
        for topic in course.get("topics", []):
            lines.append(f"   - {topic['topic']} {count_summary(topic)}")
    print("\n".join(lines))
    return 0


def export_review_set(args: argparse.Namespace) -> int:
    data = read_json_arg(args.input)
    validate_review_data(data)
    notes_root = get_notes_root(args.config, args.notes_root)
    course = required_text(data, "course")
    collection_type = normalize_collection_type(data.get("collection_type")) or "mistake_set"
    topic = str(data.get("topic", "")).strip()
    export_date = validate_export_date(
        args.date or str(data.get("source_date", "")).strip() or date.today().isoformat()
    )
    records = make_records(data)

    notes_root.mkdir(parents=True, exist_ok=True)
    course_dir = notes_root / clean_filename(course, "uncategorized")
    course_dir.mkdir(parents=True, exist_ok=True)

    filename = (
        f"{clean_filename(export_date, date.today().isoformat(), 20)}-"
        f"{clean_filename(topic, 'Mistake Notes')}-"
        f"{clean_filename(collection_type, 'mistake_set', 20)}.md"
    )
    markdown_content = render_markdown(data, export_date, records)
    markdown_path = choose_output_path(course_dir, filename, markdown_content)
    if not markdown_path.exists() or markdown_path.read_text(encoding="utf-8") != markdown_content:
        markdown_path.write_text(markdown_content, encoding="utf-8")

    print_json(
        {
            "notes_root": str(notes_root),
            "markdown_path": str(markdown_path),
            "item_count": len(records),
        }
    )
    return 0


def config_get(args: argparse.Namespace) -> int:
    print(str(get_notes_root(args.config)))
    return 0


def config_set(args: argparse.Namespace) -> int:
    notes_root = Path(args.notes_root).expanduser().resolve()
    save_config(args.config, notes_root)
    print(str(notes_root))
    return 0


def add_filter_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--query", help="Keyword or phrase to search in all saved text.")
    command.add_argument("--course", help="Filter by course name substring.")
    command.add_argument("--topic", help="Filter by topic name substring.")


def build_parser() -> argparse.ArgumentParser:
    config_option = argparse.ArgumentParser(add_help=False)
    config_option.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    storage_options = argparse.ArgumentParser(add_help=False, parents=[config_option])
    storage_options.add_argument("--db-path", help="Override database path for this run.")
    storage_options.add_argument(
        "--notes-root", help="Override the notes root (attachments and exports) for this run."
    )

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    config_parser = commands.add_parser("config", help="Read or update fixed notes root.")
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    config_get_parser = config_commands.add_parser(
        "get", parents=[config_option], help="Print the configured notes root."
    )
    config_get_parser.set_defaults(handler=config_get)
    config_set_parser = config_commands.add_parser(
        "set", parents=[config_option], help="Set the fixed notes root."
    )
    config_set_parser.add_argument("--notes-root", required=True)
    config_set_parser.set_defaults(handler=config_set)

    export_parser = commands.add_parser(
        "export", parents=[config_option], help="Export a Markdown backup from JSON."
    )
    export_parser.add_argument("--input", required=True, help="JSON file path, or '-' for stdin.")
    export_parser.add_argument("--notes-root", help="Override configured notes root for this run.")
    export_parser.add_argument("--date", help="Export date, YYYY-MM-DD. Defaults to today.")
    export_parser.set_defaults(handler=export_review_set)

    db_parser = commands.add_parser("db", help="Use the SQLite exercise database.")
    db_commands = db_parser.add_subparsers(dest="db_command", required=True)

    def add_db_command(name: str, handler: Any, help_text: str) -> argparse.ArgumentParser:
        command = db_commands.add_parser(name, parents=[storage_options], help=help_text)
        command.set_defaults(handler=handler)
        return command

    validate = add_db_command("validate", db_validate, "Validate question JSON without opening the database.")
    validate.add_argument("--input", required=True, help="JSON file path, or '-' for stdin.")
    validate.add_argument("--date", help="Source/export date, YYYY-MM-DD. Defaults to today.")

    add = add_db_command("add", db_add, "Add or update questions from JSON.")
    add.add_argument("--input", required=True, help="JSON file path, or '-' for stdin.")
    add.add_argument("--date", help="Source/export date, YYYY-MM-DD. Defaults to today.")
    add.add_argument(
        "--confirmed-selection-by-user",
        action="store_true",
        help="Required acknowledgment that the user explicitly selected which candidate items to save.",
    )

    update = add_db_command("update", db_update, "Edit one saved question in place after confirmation.")
    update.add_argument("item_id", help="Question id from db search/quiz/show.")
    update.add_argument(
        "--input", required=True, help="JSON object with only the fields to change, or '-' for stdin."
    )
    update.add_argument("--confirmed-by-user", action="store_true")

    show = add_db_command("show", db_show, "Show one question with its answer and review history.")
    show.add_argument("item_id", help="Question id from db search/quiz/due.")

    export = add_db_command(
        "export", db_export, "Export saved questions to Markdown by course and/or topic."
    )
    export.add_argument("--course", help="Exact course name to export.")
    export.add_argument("--topic", help="Exact topic name to export.")
    export.add_argument(
        "--mode",
        choices=["full", "questions-only"],
        required=True,
        help="Export full review content or questions only.",
    )
    export.add_argument("--date", help="Export date, YYYY-MM-DD. Defaults to today.")

    search = add_db_command("search", db_search, "Search questions.")
    add_filter_arguments(search)
    search.add_argument(
        "--review-status",
        choices=["pending", "done", "all"],
        default="all",
        help="Filter by review status.",
    )
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--include-content", action="store_true")

    pending = add_db_command("pending", db_pending, "List questions that are not mastered yet, oldest first.")
    add_filter_arguments(pending)
    pending.add_argument("--limit", type=int, default=3)
    pending.add_argument("--include-content", action="store_true")

    for name, handler, help_text in (
        ("quiz", db_quiz, "List quiz prompts without answers: due items first, then scheduled ones."),
        ("due", db_due, "List prompts that are due for review today, without answers."),
    ):
        review = add_db_command(name, handler, help_text)
        add_filter_arguments(review)
        review.add_argument("--limit", type=int, default=3)
        review.add_argument(
            "--shuffle", action="store_true", help="Randomize the order; due items still come first."
        )
        if name == "quiz":
            review.add_argument("--include-done", action="store_true", help="Also include mastered items.")

    grade = add_db_command("grade", db_grade, "Record a review result and schedule the next review.")
    grade.add_argument("item_id", help="Question id from db quiz/due.")
    grade.add_argument("--result", choices=REVIEW_RESULTS, required=True)
    grade.add_argument("--note", help="Brief note about the attempt, such as the mistake made.")

    done = add_db_command("mark-done", db_mark_done, "Mark one question as mastered immediately.")
    done.add_argument("item_id", help="Question id from db search/pending.")

    wrong = add_db_command(
        "mark-wrong", db_mark_wrong, "Record a wrong answer (same as grade --result wrong)."
    )
    wrong.add_argument("item_id", help="Question id from db search/pending.")
    wrong.add_argument("--note", help="Optional note about the wrong answer.")

    reset = add_db_command(
        "reset", db_reset, "Restart the review schedule for selected questions after confirmation."
    )
    reset.add_argument("item_ids", nargs="*", help="Optional question ids to reset.")
    reset.add_argument("--course", help="Exact course name to reset.")
    reset.add_argument("--topic", help="Exact topic name to reset.")
    reset.add_argument("--confirmed-by-user", action="store_true")

    attach = add_db_command("attach", db_attach, "Attach one verified image to an existing question.")
    attach.add_argument("item_id", help="Question id from db search/pending.")
    attach.add_argument("--source", required=True, help="Local source image path.")
    attach.add_argument("--role", choices=sorted(ALLOWED_ATTACHMENT_ROLES), required=True)
    attach.add_argument("--provenance", choices=sorted(ALLOWED_ATTACHMENT_PROVENANCE), required=True)
    attach.add_argument("--caption", required=True)

    attachment_update = add_db_command(
        "attachment-update", db_attachment_update, "Change stored attachment metadata after confirmation."
    )
    attachment_update.add_argument("attachment_id", type=int)
    attachment_update.add_argument("--role", choices=sorted(ALLOWED_ATTACHMENT_ROLES))
    attachment_update.add_argument("--provenance", choices=sorted(ALLOWED_ATTACHMENT_PROVENANCE))
    attachment_update.add_argument("--caption")
    attachment_update.add_argument("--confirmed-by-user", action="store_true")

    detach = add_db_command("detach", db_detach, "Remove one managed attachment after confirmation.")
    detach.add_argument("attachment_id", type=int)
    detach.add_argument("--confirmed-by-user", action="store_true")

    audit = add_db_command(
        "attachment-audit", db_attachment_audit, "Report or clean managed attachment integrity issues."
    )
    audit.add_argument("--prune-orphans", action="store_true")
    audit.add_argument("--empty-trash", action="store_true")
    audit.add_argument("--confirmed-by-user", action="store_true")

    delete = add_db_command("delete", db_delete, "Permanently delete one question by id.")
    delete.add_argument("item_id", help="Question id from db search/pending.")
    delete.add_argument("--confirmed-by-user", action="store_true")

    rename = add_db_command(
        "rename-course", db_rename_course, "Rename an existing course after explicit user confirmation."
    )
    rename.add_argument("old_course", help="Exact existing course name.")
    rename.add_argument("new_course", help="New course name.")
    rename.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required acknowledgment that the user explicitly approved this exact rename.",
    )

    stats = add_db_command("stats", db_stats, "Show per-course counts.")
    stats.add_argument("--human", action="store_true", help="Print a user-facing course list.")
    stats.add_argument("--topics", action="store_true", help="Include per-topic counts under each course.")

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except ConfirmationRequiredError as exc:
        if exc.preview is not None:
            print_json({"confirmation_required": True, "preview": exc.preview})
        return fail(str(exc), exc.exit_code)
    except CliError as exc:
        return fail(str(exc), exc.exit_code)
    except (ValueError, RuntimeError, sqlite3.Error, OSError) as exc:
        return fail(str(exc), EXIT_ERROR)


if __name__ == "__main__":
    raise SystemExit(main())
