#!/usr/bin/env python3
"""Manage course-classified mistake notes in SQLite, with Markdown backup export."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import uuid
import warnings
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


SKILL_NAME = "summarize-mistake-notes"
DEFAULT_CONFIG = Path.home() / ".codex" / "state" / SKILL_NAME / "config.json"
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


@dataclass(frozen=True)
class StorageContext:
    db_path: Path
    notes_root: Path
    attachments_root: Path


def fail(message: str, code: int = 1) -> int:
    print(message, file=sys.stderr)
    return code


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
        raise RuntimeError(
            "NOT_CONFIGURED: notes_root is missing. Ask the user for a mistake-note root folder, "
            "then run: config set --notes-root <path>"
        )
    return Path(raw).expanduser().resolve()


def get_db_path(config_path: Path, override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    config = load_config(config_path)
    raw_db = str(config.get("db_path", "")).strip()
    if raw_db:
        return Path(raw_db).expanduser().resolve()
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


def verify_source_image(source_path: str) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise ValueError(f"Attachment source is not a readable file: {source}")
    byte_size = source.stat().st_size
    if byte_size > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"Attachment exceeds {MAX_ATTACHMENT_BYTES} bytes: {source}")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                image.verify()
                image_format = str(image.format or "").upper()
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise ValueError(f"Attachment is not a valid image: {source}") from exc
    if image_format not in ATTACHMENT_MEDIA_TYPES:
        raise ValueError(f"Unsupported attachment image format: {image_format}")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    media_type, extension = ATTACHMENT_MEDIA_TYPES[image_format]
    return {
        "source": source,
        "sha256": digest,
        "media_type": media_type,
        "extension": extension,
        "byte_size": byte_size,
    }


def validated_attachment_input(raw_attachment: dict[str, Any]) -> dict[str, Any]:
    role = str(raw_attachment.get("role", "")).strip()
    provenance = str(raw_attachment.get("provenance", "")).strip()
    caption = str(raw_attachment.get("caption", "")).strip()
    if role not in ALLOWED_ATTACHMENT_ROLES:
        raise ValueError("Attachment role must be prompt or solution.")
    if provenance not in ALLOWED_ATTACHMENT_PROVENANCE:
        raise ValueError("Attachment provenance must be provided or reconstructed.")
    if not caption:
        raise ValueError("Attachment caption is required.")
    verified = verify_source_image(str(raw_attachment.get("source_path", "")).strip())
    return {
        **verified,
        "role": role,
        "provenance": provenance,
        "caption": caption,
        "original_filename": verified["source"].name,
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


def item_hash(course: str, item: dict[str, Any]) -> str:
    title = str(item.get("title", "")).strip()
    question = str(item.get("original_question", "")).strip()
    raw = f"{course}\n{title}\n{question}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:10]


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


def connect_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
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
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(review_events)").fetchall()
    }
    if "note" not in columns:
        connection.execute("ALTER TABLE review_events ADD COLUMN note TEXT")
    connection.commit()


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


def attachment_destination(
    context: StorageContext,
    question_id: str,
    attachment: dict[str, Any],
) -> tuple[Path, str]:
    stem = clean_filename(attachment["source"].stem, "image", 60)
    relative_path = (
        Path("attachments")
        / question_id
        / f"{stem}-{attachment['sha256']}{attachment['extension']}"
    )
    return context.notes_root / relative_path, relative_path.as_posix()


def copy_managed_attachment(
    context: StorageContext,
    question_id: str,
    attachment: dict[str, Any],
    created_files: list[Path],
) -> str:
    final_path, relative_path = attachment_destination(context, question_id, attachment)
    if final_path.exists():
        return relative_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = final_path.parent / f".{final_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        shutil.copyfile(attachment["source"], temporary)
        temporary.replace(final_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    created_files.append(final_path)
    return relative_path


def cleanup_created_attachment_files(created_files: list[Path], context: StorageContext) -> None:
    for path in reversed(created_files):
        if path.exists():
            path.unlink()
        parent = path.parent
        while parent != context.attachments_root.parent and parent.exists():
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
    attachment = validated_attachment_input(raw_attachment)
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
    relative_path = copy_managed_attachment(context, question_id, attachment, created_files)
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


def managed_attachment_path(context: StorageContext, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"Unsafe managed attachment path: {relative}")
    attachments_root = context.attachments_root.resolve()
    managed_path = (context.notes_root / relative_path).resolve()
    if managed_path != attachments_root and attachments_root not in managed_path.parents:
        raise ValueError(f"Managed attachment escapes attachments root: {relative}")
    return managed_path


def serialize_attachment(row: sqlite3.Row, context: StorageContext) -> dict[str, Any]:
    managed_path = managed_attachment_path(context, row["stored_relative_path"])
    return {
        "id": row["id"],
        "role": row["role"],
        "provenance": row["provenance"],
        "caption": row["caption"],
        "stored_relative_path": row["stored_relative_path"],
        "managed_path": str(managed_path),
        "media_type": row["media_type"],
        "byte_size": row["byte_size"],
        "sha256": row["sha256"],
        "missing": not managed_path.exists(),
    }


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
    digest = hashlib.sha256(managed_path.read_bytes()).hexdigest()
    if digest != attachment["sha256"]:
        raise ValueError(f"Managed attachment differs from stored digest: {managed_path}")


def stage_managed_files(
    context: StorageContext,
    paths: list[Path],
) -> tuple[Path | None, list[tuple[Path, Path]]]:
    existing_paths = [path for path in paths if path.exists()]
    if not existing_paths:
        return None, []
    trash_root = context.attachments_root / ".trash" / uuid.uuid4().hex
    moves: list[tuple[Path, Path]] = []
    for source in existing_paths:
        destination = trash_root / source.relative_to(context.attachments_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        moves.append((source, destination))
    return trash_root, moves


def restore_staged_files(moves: list[tuple[Path, Path]]) -> None:
    for original, staged in reversed(moves):
        if staged.exists():
            original.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(original)


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
            parent.rmdir()
        except OSError:
            pass


def normalize_question_for_db(
    data: dict[str, Any],
    raw_item: dict[str, Any],
    index: int,
    export_date: str,
) -> dict[str, Any]:
    course = required_text(data, "course")
    title = str(raw_item.get("title", "")).strip() or f"Question {index}"
    knowledge_points = as_list(raw_item.get("knowledge_points"))
    answer_points = as_list(raw_item.get("answer_points"))
    question = {
        "id": item_hash(course, raw_item),
        "course": course,
        "collection_type": normalize_collection_type(data.get("collection_type")) or "mistake_set",
        "topic": str(data.get("topic", "Mistake Notes")).strip() or "Mistake Notes",
        "title": title,
        "original_question": format_text(raw_item.get("original_question")),
        "knowledge_points": knowledge_points,
        "mistake_reason": format_text(raw_item.get("mistake_reason")),
        "correct_approach": format_text(raw_item.get("correct_approach")),
        "answer_points": answer_points,
        "review_suggestion": format_text(raw_item.get("review_suggestion")),
        "source_date": export_date,
    }
    question["search_text"] = "\n".join(
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
    return question


def row_to_question(row: sqlite3.Row, include_content: bool = False) -> dict[str, Any]:
    item = {
        "id": row["id"],
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
        "review_status": row["review_status"],
        "reviewed_at": row["reviewed_at"],
        "review_count": row["review_count"],
        "wrong_count": row["wrong_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_content:
        item["content"] = render_question_content(item)
    return item


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
        "wrong_count": row["wrong_count"],
        "review_count": row["review_count"],
    }


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
        attachment["caption"]
        .replace("\\", r"\\")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )
    relative_path = (Path("..") / Path(attachment["stored_relative_path"])).as_posix()
    return f"![{caption}]({relative_path})"


def render_db_question_export(
    items: list[dict[str, Any]],
    *,
    mode: str,
    course: str | None,
    topic: str | None,
    export_date: str,
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


def insert_questions(
    connection: sqlite3.Connection,
    data: dict[str, Any],
    export_date: str,
    context: StorageContext | None = None,
    created_files: list[Path] | None = None,
) -> tuple[int, int, list[str]]:
    validate_review_data(data)
    course = required_text(data, "course")
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("items must be a non-empty list.")

    course_id = get_or_create_course(connection, course)
    timestamp = now_iso()
    inserted = 0
    updated = 0
    ids: list[str] = []

    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, dict):
            raise ValueError(f"Item {index} must be an object.")
        question = normalize_question_for_db(data, raw_item, index, export_date)
        existing = connection.execute(
            "SELECT id FROM questions WHERE id = ?", (question["id"],)
        ).fetchone()
        ids.append(question["id"])

        values = (
            question["id"],
            course_id,
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
            timestamp,
            timestamp,
        )
        connection.execute(
            """
            INSERT INTO questions (
                id, course_id, collection_type, topic, title, original_question,
                knowledge_points_json, mistake_reason, correct_approach,
                answer_points_json, review_suggestion, source_date, search_text,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                course_id = excluded.course_id,
                collection_type = excluded.collection_type,
                topic = excluded.topic,
                title = excluded.title,
                original_question = excluded.original_question,
                knowledge_points_json = excluded.knowledge_points_json,
                mistake_reason = excluded.mistake_reason,
                correct_approach = excluded.correct_approach,
                answer_points_json = excluded.answer_points_json,
                review_suggestion = excluded.review_suggestion,
                source_date = excluded.source_date,
                search_text = excluded.search_text,
                updated_at = excluded.updated_at
            """,
            values,
        )
        if existing:
            updated += 1
        else:
            inserted += 1
        attachments = raw_item.get("attachments", [])
        if attachments:
            if context is None or created_files is None:
                raise ValueError("Attachment storage context is required when adding images.")
            for raw_attachment in attachments:
                add_attachment_row(
                    connection,
                    context,
                    question["id"],
                    raw_attachment,
                    created_files,
                )

    return inserted, updated, ids


def build_question_query(
    *,
    query: str | None,
    course: str | None,
    review_status: str,
    limit: int,
) -> tuple[str, list[Any]]:
    where = []
    params: list[Any] = []
    if query:
        where.append("q.search_text LIKE ?")
        params.append(f"%{query}%")
    if course:
        where.append("c.name LIKE ?")
        params.append(f"%{course}%")
    if review_status != "all":
        where.append("q.review_status = ?")
        params.append(review_status)
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    params.append(limit)
    sql = f"""
        SELECT q.*, c.name AS course
        FROM questions q
        JOIN courses c ON c.id = q.course_id
        {where_sql}
        ORDER BY q.source_date ASC, q.created_at ASC, q.id ASC
        LIMIT ?
    """
    return sql, params


def db_add(args: argparse.Namespace) -> int:
    created_files: list[Path] = []
    context: StorageContext | None = None
    try:
        if not args.confirmed_selection_by_user:
            raise ValueError(
                "Adding questions requires explicit user selection. "
                "Use --confirmed-selection-by-user only after the user selects the candidate items to save."
            )
        data = read_json_arg(args.input)
        validate_review_data(data)
        export_date = validate_export_date(
            args.date or str(data.get("source_date", "")).strip() or date.today().isoformat()
        )
        context = resolve_storage_context(args.config, args.db_path, args.notes_root)
        db_path = context.db_path
        with connect_db(db_path) as connection:
            try:
                inserted, updated, ids = insert_questions(
                    connection, data, export_date, context, created_files
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
    except (RuntimeError, ValueError, sqlite3.Error, OSError) as exc:
        if context is not None:
            cleanup_created_attachment_files(created_files, context)
        return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    result = {
        "db_path": str(db_path),
        "inserted": inserted,
        "updated": updated,
        "ids": ids,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def db_validate(args: argparse.Namespace) -> int:
    try:
        data = read_json_arg(args.input)
        validate_review_data(data)
        export_date = validate_export_date(
            args.date or str(data.get("source_date", "")).strip() or date.today().isoformat()
        )
        raw_items = data["items"]
    except (RuntimeError, ValueError) as exc:
        return fail(str(exc), 1)

    result = {
        "valid": True,
        "course": str(data["course"]).strip(),
        "collection_type": normalize_collection_type(data.get("collection_type")),
        "topic": str(data["topic"]).strip(),
        "source_date": export_date,
        "item_count": len(raw_items),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def db_search(args: argparse.Namespace) -> int:
    try:
        if args.limit < 1:
            raise ValueError("--limit must be at least 1.")
        context = resolve_storage_context(args.config, args.db_path, args.notes_root)
        db_path = context.db_path
        with connect_db(db_path) as connection:
            sql, params = build_question_query(
                query=args.query,
                course=args.course,
                review_status=args.review_status,
                limit=args.limit,
            )
            rows = connection.execute(sql, params).fetchall()
            attachment_map = load_attachments(connection, context, [row["id"] for row in rows])
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    items = [row_to_question(row, include_content=False) for row in rows]
    add_attachments_to_items(items, attachment_map)
    if args.include_content:
        for item in items:
            item["content"] = render_question_content(item)
    result = {
        "db_path": str(db_path),
        "returned_count": len(items),
        "items": items,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def db_attach(args: argparse.Namespace) -> int:
    created_files: list[Path] = []
    context: StorageContext | None = None
    try:
        context = resolve_storage_context(args.config, args.db_path, args.notes_root)
        with connect_db(context.db_path) as connection:
            try:
                question = connection.execute(
                    "SELECT id FROM questions WHERE id = ?", (args.item_id,)
                ).fetchone()
                if question is None:
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
            except Exception:
                connection.rollback()
                raise
    except (RuntimeError, ValueError, sqlite3.Error, OSError) as exc:
        if context is not None:
            cleanup_created_attachment_files(created_files, context)
        return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    result = {
        "db_path": str(context.db_path),
        "item_id": args.item_id,
        "attachment": serialize_attachment(attachment, context),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def db_attachment_update(args: argparse.Namespace) -> int:
    try:
        if not args.confirmed_by_user:
            raise ValueError(
                "Attachment metadata changes require explicit user confirmation. "
                "Use --confirmed-by-user only after the user approves the change."
            )
        updates: list[str] = []
        params: list[Any] = []
        for field in ("role", "provenance", "caption"):
            value = getattr(args, field)
            if value is not None:
                normalized = str(value).strip()
                if not normalized:
                    raise ValueError(f"Attachment {field} cannot be empty.")
                updates.append(f"{field} = ?")
                params.append(normalized)
        if not updates:
            raise ValueError("At least one attachment metadata field must be updated.")
        context = resolve_storage_context(args.config, args.db_path, args.notes_root)
        with connect_db(context.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM question_attachments WHERE id = ?", (args.attachment_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Attachment not found: {args.attachment_id}")
            params.append(args.attachment_id)
            connection.execute(
                f"UPDATE question_attachments SET {', '.join(updates)} WHERE id = ?", params
            )
            connection.commit()
            updated = connection.execute(
                "SELECT * FROM question_attachments WHERE id = ?", (args.attachment_id,)
            ).fetchone()
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    print(
        json.dumps(
            {"db_path": str(context.db_path), "attachment": serialize_attachment(updated, context)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def db_detach(args: argparse.Namespace) -> int:
    trash_root: Path | None = None
    moves: list[tuple[Path, Path]] = []
    managed_paths: list[Path] = []
    try:
        if not args.confirmed_by_user:
            raise ValueError(
                "Detaching an attachment requires explicit user confirmation. "
                "Use --confirmed-by-user only after the user approves deletion."
            )
        context = resolve_storage_context(args.config, args.db_path, args.notes_root)
        with connect_db(context.db_path) as connection:
            row = connection.execute(
                "SELECT * FROM question_attachments WHERE id = ?", (args.attachment_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Attachment not found: {args.attachment_id}")
            attachment = serialize_attachment(row, context)
            managed_paths = [Path(attachment["managed_path"])]
            trash_root, moves = stage_managed_files(context, managed_paths)
            try:
                connection.execute(
                    "DELETE FROM question_attachments WHERE id = ?", (args.attachment_id,)
                )
                connection.commit()
            except Exception:
                connection.rollback()
                restore_staged_files(moves)
                raise
        cleanup_warning = finalize_trash_cleanup(trash_root)
        remove_empty_question_dirs(context, managed_paths)
    except (RuntimeError, ValueError, sqlite3.Error, OSError) as exc:
        return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    result = {"db_path": str(context.db_path), "detached_attachment": attachment}
    if cleanup_warning:
        result["cleanup_warning"] = cleanup_warning
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def db_attachment_audit(args: argparse.Namespace) -> int:
    try:
        if (args.prune_orphans or args.empty_trash) and not args.confirmed_by_user:
            raise ValueError(
                "Attachment cleanup requires explicit user confirmation. "
                "Use --confirmed-by-user only after the user approves deletion."
            )
        context = resolve_storage_context(args.config, args.db_path, args.notes_root)
        with connect_db(context.db_path) as connection:
            rows = connection.execute(
                "SELECT * FROM question_attachments ORDER BY question_id, role, id"
            ).fetchall()
        referenced: set[Path] = set()
        missing_files: list[dict[str, Any]] = []
        modified_files: list[dict[str, Any]] = []
        for row in rows:
            attachment = serialize_attachment(row, context)
            path = Path(attachment["managed_path"])
            referenced.add(path)
            if attachment["missing"]:
                missing_files.append(attachment)
            elif hashlib.sha256(path.read_bytes()).hexdigest() != attachment["sha256"]:
                modified_files.append(attachment)
        orphan_paths: list[Path] = []
        trash_paths: list[Path] = []
        if context.attachments_root.exists():
            for path in context.attachments_root.rglob("*"):
                if not path.is_file():
                    continue
                relative_parts = path.relative_to(context.attachments_root).parts
                if relative_parts and relative_parts[0] == ".trash":
                    trash_paths.append(path)
                elif path.resolve() not in referenced:
                    orphan_paths.append(path)
        if args.prune_orphans:
            for path in orphan_paths:
                path.unlink()
        if args.empty_trash and (context.attachments_root / ".trash").exists():
            shutil.rmtree(context.attachments_root / ".trash")
    except (RuntimeError, ValueError, sqlite3.Error, OSError) as exc:
        return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    result = {
        "db_path": str(context.db_path),
        "missing_files": missing_files,
        "modified_files": modified_files,
        "orphan_files": [str(path) for path in orphan_paths],
        "trash_files": [str(path) for path in trash_paths],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def db_pending(args: argparse.Namespace) -> int:
    args.review_status = "pending"
    args.query = args.query or None
    return db_search(args)


def db_export(args: argparse.Namespace) -> int:
    try:
        course = str(args.course or "").strip() or None
        topic = str(args.topic or "").strip() or None
        if not course and not topic:
            raise ValueError("At least one of --course or --topic is required for export.")
        export_date = validate_export_date(args.date or date.today().isoformat())
        context = resolve_storage_context(args.config, args.db_path, args.notes_root)
        db_path = context.db_path
        where: list[str] = []
        params: list[Any] = []
        if course:
            where.append("c.name = ?")
            params.append(course)
        if topic:
            where.append("q.topic = ?")
            params.append(topic)
        with connect_db(db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT q.*, c.name AS course
                FROM questions q
                JOIN courses c ON c.id = q.course_id
                WHERE {" AND ".join(where)}
                ORDER BY q.source_date ASC, q.created_at ASC, q.id ASC
                """,
                params,
            ).fetchall()
            attachment_map = load_attachments(connection, context, [row["id"] for row in rows])
        if not rows:
            raise ValueError("No questions matched the export filters.")
        items = [row_to_question(row, include_content=False) for row in rows]
        add_attachments_to_items(items, attachment_map)
        for item in items:
            for attachment in item.get("attachments", []):
                if args.mode == "full" or attachment["role"] == "prompt":
                    verify_managed_attachment(attachment)
        markdown_content = render_db_question_export(
            items,
            mode=args.mode,
            course=course,
            topic=topic,
            export_date=export_date,
        )
        scope_parts = [part for part in (course, topic) if part]
        scope_label = "-".join(clean_filename(part, "questions") for part in scope_parts)
        export_dir = context.notes_root / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{export_date}-{scope_label}-{args.mode}.md"
        markdown_path = choose_output_path(export_dir, filename, markdown_content)
        if not markdown_path.exists() or markdown_path.read_text(encoding="utf-8") != markdown_content:
            markdown_path.write_text(markdown_content, encoding="utf-8")
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    result = {
        "db_path": str(db_path),
        "markdown_path": str(markdown_path),
        "item_count": len(items),
        "mode": args.mode,
        "course": course,
        "topic": topic,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def db_quiz(args: argparse.Namespace) -> int:
    try:
        if args.limit < 1:
            raise ValueError("--limit must be at least 1.")
        context = resolve_storage_context(args.config, args.db_path, args.notes_root)
        db_path = context.db_path
        with connect_db(db_path) as connection:
            sql, params = build_question_query(
                query=args.query,
                course=args.course,
                review_status="pending",
                limit=args.limit,
            )
            rows = connection.execute(sql, params).fetchall()
            attachment_map = load_attachments(
                connection, context, [row["id"] for row in rows], role="prompt"
            )
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    items = [row_to_quiz_prompt(row) for row in rows]
    add_attachments_to_items(items, attachment_map)
    result = {
        "db_path": str(db_path),
        "mode": args.db_command,
        "returned_count": len(rows),
        "items": items,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def update_review_state(args: argparse.Namespace, status: str, event_result: str) -> int:
    try:
        db_path = get_db_path(args.config, args.db_path)
        timestamp = now_iso()
        note = str(getattr(args, "note", "") or "").strip() or None
        with connect_db(db_path) as connection:
            row = connection.execute(
                """
                SELECT q.*, c.name AS course
                FROM questions q
                JOIN courses c ON c.id = q.course_id
                WHERE q.id = ?
                """,
                (args.item_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Question not found: {args.item_id}")
            if status == "done":
                connection.execute(
                    """
                    UPDATE questions
                    SET review_status = 'done',
                        reviewed_at = ?,
                        review_count = review_count + 1,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (timestamp, timestamp, args.item_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE questions
                    SET review_status = 'pending',
                        wrong_count = wrong_count + 1,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (timestamp, args.item_id),
                )
            connection.execute(
                "INSERT INTO review_events(question_id, result, note, created_at) VALUES (?, ?, ?, ?)",
                (args.item_id, event_result, note, timestamp),
            )
            connection.commit()
            updated_row = connection.execute(
                """
                SELECT q.*, c.name AS course
                FROM questions q
                JOIN courses c ON c.id = q.course_id
                WHERE q.id = ?
                """,
                (args.item_id,),
            ).fetchone()
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    result = {
        "db_path": str(db_path),
        "item": row_to_question(updated_row, include_content=False),
        "event": {
            "result": event_result,
            "note": note,
            "created_at": timestamp,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def db_delete(args: argparse.Namespace) -> int:
    trash_root: Path | None = None
    moves: list[tuple[Path, Path]] = []
    managed_paths: list[Path] = []
    try:
        if not args.confirmed_by_user:
            raise ValueError(
                "Deleting a question requires explicit user confirmation. "
                "Use --confirmed-by-user only after the user approves permanent deletion."
            )
        context = resolve_storage_context(args.config, args.db_path, args.notes_root)
        db_path = context.db_path
        with connect_db(db_path) as connection:
            row = connection.execute(
                """
                SELECT q.*, c.name AS course
                FROM questions q
                JOIN courses c ON c.id = q.course_id
                WHERE q.id = ?
                """,
                (args.item_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Question not found: {args.item_id}")
            deleted_item = row_to_question(row, include_content=False)
            attachment_rows = connection.execute(
                "SELECT * FROM question_attachments WHERE question_id = ? ORDER BY id",
                (args.item_id,),
            ).fetchall()
            attachments = [serialize_attachment(item, context) for item in attachment_rows]
            managed_paths = [Path(attachment["managed_path"]) for attachment in attachments]
            if attachments:
                deleted_item["attachments"] = attachments
            trash_root, moves = stage_managed_files(context, managed_paths)
            try:
                connection.execute("DELETE FROM questions WHERE id = ?", (args.item_id,))
                connection.commit()
            except Exception:
                connection.rollback()
                restore_staged_files(moves)
                raise
        cleanup_warning = finalize_trash_cleanup(trash_root)
        remove_empty_question_dirs(context, managed_paths)
    except (RuntimeError, ValueError, sqlite3.Error, OSError) as exc:
        return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    result = {
        "db_path": str(db_path),
        "deleted_item": deleted_item,
    }
    if cleanup_warning:
        result["cleanup_warning"] = cleanup_warning
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def db_rename_course(args: argparse.Namespace) -> int:
    try:
        old_course = str(args.old_course).strip()
        new_course = str(args.new_course).strip()
        if not old_course or not new_course:
            raise ValueError("Both old and new course names are required.")
        if old_course == new_course:
            raise ValueError("Old and new course names must be different.")
        if not args.confirmed_by_user:
            raise ValueError(
                "Course rename requires explicit user confirmation. "
                "Use --confirmed-by-user only after the user approves the exact rename."
            )

        db_path = get_db_path(args.config, args.db_path)
        timestamp = now_iso()
        with connect_db(db_path) as connection:
            source = connection.execute(
                "SELECT id FROM courses WHERE name = ?", (old_course,)
            ).fetchone()
            if source is None:
                raise ValueError(f"Course not found: {old_course}")
            target = connection.execute(
                "SELECT id FROM courses WHERE name = ?", (new_course,)
            ).fetchone()
            if target is not None:
                raise ValueError(f"Target course already exists: {new_course}")

            course_id = int(source["id"])
            questions = connection.execute(
                "SELECT id, search_text FROM questions WHERE course_id = ?", (course_id,)
            ).fetchall()
            old_prefix = f"{old_course}\n"
            for question in questions:
                search_text = str(question["search_text"])
                if not search_text.startswith(old_prefix):
                    raise ValueError(
                        f"Cannot safely update search index for question: {question['id']}"
                    )

            connection.execute(
                "UPDATE courses SET name = ? WHERE id = ?", (new_course, course_id)
            )
            for question in questions:
                updated_search_text = f"{new_course}\n{question['search_text'][len(old_prefix):]}"
                connection.execute(
                    "UPDATE questions SET search_text = ?, updated_at = ? WHERE id = ?",
                    (updated_search_text, timestamp, question["id"]),
                )
            connection.commit()
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    result = {
        "db_path": str(db_path),
        "old_course": old_course,
        "new_course": new_course,
        "renamed_questions": len(questions),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def db_stats(args: argparse.Namespace) -> int:
    try:
        db_path = get_db_path(args.config, args.db_path)
        with connect_db(db_path) as connection:
            rows = connection.execute(
                """
                SELECT c.name AS course,
                       COUNT(*) AS total,
                       SUM(CASE WHEN q.review_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                       SUM(CASE WHEN q.review_status = 'done' THEN 1 ELSE 0 END) AS done
                FROM questions q
                JOIN courses c ON c.id = q.course_id
                GROUP BY c.name
                ORDER BY c.name
                """
            ).fetchall()
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    if getattr(args, "human", False):
        if not rows:
            print("No courses are recorded in the exercise bank yet.")
        else:
            lines = ["Available courses:"]
            for index, row in enumerate(rows, start=1):
                lines.append(
                    f"{index}. {row['course']} ({row['pending']} pending / {row['total']} total)"
                )
            print("\n".join(lines))
    else:
        result = {
            "db_path": str(db_path),
            "courses": [dict(row) for row in rows],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def export_review_set(args: argparse.Namespace) -> int:
    try:
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

    except (RuntimeError, ValueError) as exc:
        return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    result = {
        "notes_root": str(notes_root),
        "markdown_path": str(markdown_path),
        "item_count": len(records),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    config_parser = subparsers.add_parser("config", help="Read or update fixed notes root.")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)

    config_get = config_subparsers.add_parser("get", help="Print the configured notes root.")
    config_get.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    config_set = config_subparsers.add_parser("set", help="Set the fixed notes root.")
    config_set.add_argument("--notes-root", required=True)
    config_set.add_argument("--config", type=Path, default=DEFAULT_CONFIG)

    export_parser = subparsers.add_parser("export", help="Export a Markdown backup from JSON.")
    export_parser.add_argument("--input", required=True, help="JSON file path, or '-' for stdin.")
    export_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    export_parser.add_argument("--notes-root", help="Override configured notes root for this run.")
    export_parser.add_argument("--date", help="Export date, YYYY-MM-DD. Defaults to today.")

    db_parser = subparsers.add_parser("db", help="Use the SQLite exercise database.")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)

    db_validate_parser = db_subparsers.add_parser("validate", help="Validate question JSON.")
    db_validate_parser.add_argument("--input", required=True, help="JSON file path, or '-' for stdin.")
    db_validate_parser.add_argument("--date", help="Source/export date, YYYY-MM-DD. Defaults to today.")

    db_add_parser = db_subparsers.add_parser("add", help="Add or update questions from JSON.")
    db_add_parser.add_argument("--input", required=True, help="JSON file path, or '-' for stdin.")
    db_add_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    db_add_parser.add_argument("--db-path", help="Override database path for this run.")
    db_add_parser.add_argument("--notes-root", help="Override managed attachment root for this run.")
    db_add_parser.add_argument("--date", help="Source/export date, YYYY-MM-DD. Defaults to today.")
    db_add_parser.add_argument(
        "--confirmed-selection-by-user",
        action="store_true",
        help="Required acknowledgment that the user explicitly selected which candidate items to save.",
    )

    db_export_parser = db_subparsers.add_parser(
        "export", help="Export saved questions to Markdown by course and/or topic."
    )
    db_export_parser.add_argument("--course", help="Exact course name to export.")
    db_export_parser.add_argument("--topic", help="Exact topic name to export.")
    db_export_parser.add_argument(
        "--mode",
        choices=["full", "questions-only"],
        required=True,
        help="Export full review content or questions only.",
    )
    db_export_parser.add_argument("--date", help="Export date, YYYY-MM-DD. Defaults to today.")
    db_export_parser.add_argument("--notes-root", help="Override configured notes root for output.")
    db_export_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    db_export_parser.add_argument("--db-path", help="Override database path for this run.")

    db_search_parser = db_subparsers.add_parser("search", help="Search questions.")
    db_search_parser.add_argument("--query", help="Keyword or phrase to search.")
    db_search_parser.add_argument("--course", help="Filter by course name substring.")
    db_search_parser.add_argument(
        "--review-status",
        choices=["pending", "done", "all"],
        default="all",
        help="Filter by review status.",
    )
    db_search_parser.add_argument("--limit", type=int, default=10)
    db_search_parser.add_argument("--include-content", action="store_true")
    db_search_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    db_search_parser.add_argument("--db-path", help="Override database path for this run.")
    db_search_parser.add_argument("--notes-root", help="Override managed attachment root for this run.")

    db_pending_parser = db_subparsers.add_parser(
        "pending", help="List unchecked questions, oldest first."
    )
    db_pending_parser.add_argument("--query", help="Optional keyword or phrase to search.")
    db_pending_parser.add_argument("--course", help="Filter by course name substring.")
    db_pending_parser.add_argument("--limit", type=int, default=3)
    db_pending_parser.add_argument("--include-content", action="store_true")
    db_pending_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    db_pending_parser.add_argument("--db-path", help="Override database path for this run.")
    db_pending_parser.add_argument("--notes-root", help="Override managed attachment root for this run.")

    db_attach_parser = db_subparsers.add_parser(
        "attach", help="Attach one verified image to an existing question."
    )
    db_attach_parser.add_argument("item_id", help="Question id from db search/pending.")
    db_attach_parser.add_argument("--source", required=True, help="Local source image path.")
    db_attach_parser.add_argument("--role", choices=sorted(ALLOWED_ATTACHMENT_ROLES), required=True)
    db_attach_parser.add_argument(
        "--provenance", choices=sorted(ALLOWED_ATTACHMENT_PROVENANCE), required=True
    )
    db_attach_parser.add_argument("--caption", required=True)
    db_attach_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    db_attach_parser.add_argument("--db-path", help="Override database path for this run.")
    db_attach_parser.add_argument("--notes-root", help="Override managed attachment root for this run.")

    db_attachment_update_parser = db_subparsers.add_parser(
        "attachment-update", help="Change stored attachment metadata after confirmation."
    )
    db_attachment_update_parser.add_argument("attachment_id", type=int)
    db_attachment_update_parser.add_argument("--role", choices=sorted(ALLOWED_ATTACHMENT_ROLES))
    db_attachment_update_parser.add_argument(
        "--provenance", choices=sorted(ALLOWED_ATTACHMENT_PROVENANCE)
    )
    db_attachment_update_parser.add_argument("--caption")
    db_attachment_update_parser.add_argument("--confirmed-by-user", action="store_true")
    db_attachment_update_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    db_attachment_update_parser.add_argument("--db-path", help="Override database path for this run.")
    db_attachment_update_parser.add_argument(
        "--notes-root", help="Override managed attachment root for this run."
    )

    db_detach_parser = db_subparsers.add_parser(
        "detach", help="Remove one managed attachment after confirmation."
    )
    db_detach_parser.add_argument("attachment_id", type=int)
    db_detach_parser.add_argument("--confirmed-by-user", action="store_true")
    db_detach_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    db_detach_parser.add_argument("--db-path", help="Override database path for this run.")
    db_detach_parser.add_argument("--notes-root", help="Override managed attachment root for this run.")

    db_audit_parser = db_subparsers.add_parser(
        "attachment-audit", help="Report or clean managed attachment integrity issues."
    )
    db_audit_parser.add_argument("--prune-orphans", action="store_true")
    db_audit_parser.add_argument("--empty-trash", action="store_true")
    db_audit_parser.add_argument("--confirmed-by-user", action="store_true")
    db_audit_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    db_audit_parser.add_argument("--db-path", help="Override database path for this run.")
    db_audit_parser.add_argument("--notes-root", help="Override managed attachment root for this run.")

    for command_name, help_text in (
        ("quiz", "List unanswered quiz prompts without answer content."),
        ("due", "List due review prompts without answer content."),
    ):
        db_quiz_parser = db_subparsers.add_parser(command_name, help=help_text)
        db_quiz_parser.add_argument("--query", help="Optional keyword or phrase to search.")
        db_quiz_parser.add_argument("--course", help="Filter by course name substring.")
        db_quiz_parser.add_argument("--limit", type=int, default=3)
        db_quiz_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
        db_quiz_parser.add_argument("--db-path", help="Override database path for this run.")
        db_quiz_parser.add_argument("--notes-root", help="Override managed attachment root for this run.")

    db_done_parser = db_subparsers.add_parser(
        "mark-done", help="Mark one question as reviewed."
    )
    db_done_parser.add_argument("item_id", help="Question id from db search/pending.")
    db_done_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    db_done_parser.add_argument("--db-path", help="Override database path for this run.")

    db_wrong_parser = db_subparsers.add_parser(
        "mark-wrong", help="Record a wrong answer and keep the question pending."
    )
    db_wrong_parser.add_argument("item_id", help="Question id from db search/pending.")
    db_wrong_parser.add_argument("--note", help="Optional note about the wrong answer.")
    db_wrong_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    db_wrong_parser.add_argument("--db-path", help="Override database path for this run.")

    db_delete_parser = db_subparsers.add_parser(
        "delete", help="Permanently delete one question by id."
    )
    db_delete_parser.add_argument("item_id", help="Question id from db search/pending.")
    db_delete_parser.add_argument("--confirmed-by-user", action="store_true")
    db_delete_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    db_delete_parser.add_argument("--db-path", help="Override database path for this run.")
    db_delete_parser.add_argument("--notes-root", help="Override managed attachment root for this run.")

    db_rename_parser = db_subparsers.add_parser(
        "rename-course", help="Rename an existing course after explicit user confirmation."
    )
    db_rename_parser.add_argument("old_course", help="Exact existing course name.")
    db_rename_parser.add_argument("new_course", help="New course name.")
    db_rename_parser.add_argument(
        "--confirmed-by-user",
        action="store_true",
        help="Required acknowledgment that the user explicitly approved this exact rename.",
    )
    db_rename_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    db_rename_parser.add_argument("--db-path", help="Override database path for this run.")

    db_stats_parser = db_subparsers.add_parser("stats", help="Show per-course counts.")
    db_stats_parser.add_argument("--human", action="store_true", help="Print a user-facing course list.")
    db_stats_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    db_stats_parser.add_argument("--db-path", help="Override database path for this run.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "config":
        try:
            if args.config_command == "get":
                notes_root = get_notes_root(args.config)
                print(str(notes_root))
                return 0
            if args.config_command == "set":
                notes_root = Path(args.notes_root).expanduser().resolve()
                save_config(args.config, notes_root)
                print(str(notes_root))
                return 0
        except (RuntimeError, ValueError) as exc:
            return fail(str(exc), 2 if str(exc).startswith("NOT_CONFIGURED") else 1)

    if args.command == "export":
        return export_review_set(args)

    if args.command == "db":
        if args.db_command == "validate":
            return db_validate(args)
        if args.db_command == "add":
            return db_add(args)
        if args.db_command == "export":
            return db_export(args)
        if args.db_command == "search":
            return db_search(args)
        if args.db_command == "pending":
            return db_pending(args)
        if args.db_command == "attach":
            return db_attach(args)
        if args.db_command == "attachment-update":
            return db_attachment_update(args)
        if args.db_command == "detach":
            return db_detach(args)
        if args.db_command == "attachment-audit":
            return db_attachment_audit(args)
        if args.db_command in {"quiz", "due"}:
            return db_quiz(args)
        if args.db_command == "mark-done":
            return update_review_state(args, "done", "correct")
        if args.db_command == "mark-wrong":
            return update_review_state(args, "pending", "wrong")
        if args.db_command == "delete":
            return db_delete(args)
        if args.db_command == "rename-course":
            return db_rename_course(args)
        if args.db_command == "stats":
            return db_stats(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
