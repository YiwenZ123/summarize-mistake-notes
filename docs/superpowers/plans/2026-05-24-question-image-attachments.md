# Question Image Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add verified, role-aware image attachments to the SQLite exercise bank while preserving text-only behavior and keeping solution images out of self-test flows.

**Architecture:** Keep the existing single-script CLI and add a `question_attachments` child table plus focused storage, validation, rendering, and destructive-operation helpers in `scripts/export_review_set.py`. Store images as managed files under the resolved notes root, expose attachment metadata through existing query output, and use explicit commands for metadata changes, detach, delete, and audit.

**Tech Stack:** Python 3.12, SQLite, `pathlib`/`hashlib`/`shutil`/`uuid`, Pillow (`PIL`), `unittest`, Markdown exports.

---

## File Map

- Modify `scripts/export_review_set.py`: attachment schema, validated image handling, storage-context resolution, query/export integration, maintenance commands, rollback, and audit.
- Modify `tests/test_export_review_set.py`: CLI regression tests, real generated image fixtures, safety and visibility tests.
- Modify `references/schema.md`: documented optional attachment input object and allowed values.
- Modify `SKILL.md`: attachment decision rules, quiz visibility policy, and authorization rules.
- Keep `docs/superpowers/specs/2026-05-24-question-image-attachments-design.md` as the approved requirements source.

### Task 1: Establish Attachment Schema, Context, And Validation

**Files:**
- Modify: `tests/test_export_review_set.py`
- Modify: `scripts/export_review_set.py`

- [ ] **Step 1: Write failing tests for schema migration, root resolution, and input validation**

Add a small image-fixture helper and tests that create real images without external files:

```python
from PIL import Image


def write_image(path: Path, image_format: str = "PNG") -> Path:
    Image.new("RGB", (4, 4), color=(20, 40, 60)).save(path, format=image_format)
    return path


class AttachmentFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "exercise_bank.sqlite3"
        self.image_path = write_image(self.root / "diagram.png")

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [str(PYTHON), str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_text_only_add_creates_attachment_table_without_changing_item_shape(self):
        added = self.run_cli(
            "db", "add", "--input", str(self.make_text_input()),
            "--confirmed-selection-by-user", "--db-path", str(self.db_path)
        )
        self.assertEqual(0, added.returncode, added.stderr)
        found = self.run_cli("db", "search", "--db-path", str(self.db_path))
        self.assertNotIn("attachments", json.loads(found.stdout)["items"][0])
        with sqlite3.connect(self.db_path) as connection:
            names = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertIn("question_attachments", names)

    def test_validate_rejects_non_image_renamed_png_without_writing_files(self):
        bad = self.root / "fake.png"
        bad.write_text("not an image", encoding="utf-8")
        payload = self.make_attachment_input(bad, role="prompt")
        validated = self.run_cli("db", "validate", "--input", str(payload))
        self.assertNotEqual(0, validated.returncode)
        self.assertIn("valid image", validated.stderr)
        self.assertFalse((self.root / "attachments").exists())
```

`make_text_input()` and `make_attachment_input()` create the same required
question fields already used by existing fixtures, with the latter adding:

```python
"attachments": [{
    "source_path": str(source_path),
    "role": role,
    "provenance": "provided",
    "caption": "Network diagram",
}]
```

- [ ] **Step 2: Run the new foundation tests and observe failure**

Run:

```powershell
& '<python>' -m unittest tests.test_export_review_set.AttachmentFoundationTests -v
```

Expected: failures because `question_attachments` is not created and
attachment validation has not been implemented.

- [ ] **Step 3: Add foundational constants, schema, storage context, and verified-image normalization**

Implement the following interfaces in `scripts/export_review_set.py`:

```python
import hashlib
import shutil
import uuid
import warnings
from dataclasses import dataclass
from PIL import Image, UnidentifiedImageError

ALLOWED_ATTACHMENT_ROLES = {"prompt", "solution"}
ALLOWED_ATTACHMENT_PROVENANCE = {"provided", "reconstructed"}
MEDIA_TYPES = {"PNG": ("image/png", ".png"), "JPEG": ("image/jpeg", ".jpg"), "WEBP": ("image/webp", ".webp")}
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class StorageContext:
    db_path: Path
    notes_root: Path
    attachments_root: Path


def resolve_storage_context(config_path: Path, db_override: str | None, notes_override: str | None) -> StorageContext:
    db_path = get_db_path(config_path, db_override)
    if notes_override:
        notes_root = Path(notes_override).expanduser().resolve()
    elif db_override:
        notes_root = db_path.parent
    else:
        notes_root = get_notes_root(config_path)
    return StorageContext(db_path, notes_root, notes_root / "attachments")


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
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError(f"Attachment is not a valid image: {source}") from exc
    if image_format not in MEDIA_TYPES:
        raise ValueError(f"Unsupported attachment image format: {image_format}")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    media_type, extension = MEDIA_TYPES[image_format]
    return {"source": source, "sha256": digest, "media_type": media_type, "extension": extension, "byte_size": byte_size}
```

Extend `ensure_schema()` with the approved `question_attachments` table and
index. Extend `validate_review_data()` so optional attachment objects require
`source_path`, `caption`, `role`, and `provenance`, and call
`verify_source_image()` without copying files.

- [ ] **Step 4: Run foundation and existing regression tests**

Run:

```powershell
& '<python>' -m unittest tests.test_export_review_set.AttachmentFoundationTests tests.test_export_review_set.AddQuestionSelectionTests tests.test_export_review_set.ExportQuestionsTests -v
```

Expected: all selected tests pass and text-only outputs retain the prior
shape.

- [ ] **Step 5: Commit the foundation change**

```powershell
git add scripts/export_review_set.py tests/test_export_review_set.py
git commit -m "feat: add attachment schema and image validation"
```

### Task 2: Import And Attach Verified Managed Files

**Files:**
- Modify: `tests/test_export_review_set.py`
- Modify: `scripts/export_review_set.py`

- [ ] **Step 1: Write failing import, deduplication, and rollback tests**

Add tests with an image-bearing input and a two-item import:

```python
class AttachmentImportTests(unittest.TestCase):
    def test_add_with_prompt_image_copies_file_and_returns_metadata(self):
        added = self.run_attachment_add(self.image_path)
        self.assertEqual(0, added.returncode, added.stderr)
        item = json.loads(self.run_cli("db", "search", "--db-path", str(self.db_path)).stdout)["items"][0]
        attachment = item["attachments"][0]
        self.assertEqual("prompt", attachment["role"])
        self.assertEqual("image/png", attachment["media_type"])
        self.assertTrue(Path(attachment["managed_path"]).exists())
        self.assertEqual(self.root / "attachments", Path(attachment["managed_path"]).parents[1])

    def test_add_duplicate_image_keeps_one_managed_copy(self):
        self.assertEqual(0, self.run_attachment_add(self.image_path).returncode)
        self.assertEqual(0, self.run_attachment_add(self.image_path).returncode)
        item = json.loads(self.run_cli("db", "search", "--db-path", str(self.db_path)).stdout)["items"][0]
        self.assertEqual(1, len(item["attachments"]))
        self.assertEqual(1, len(list((self.root / "attachments").rglob("*.png"))))

    def test_add_rolls_back_files_when_later_attachment_is_invalid(self):
        input_path = self.make_two_attachment_input(self.image_path, self.root / "missing.png")
        added = self.run_cli(
            "db", "add", "--input", str(input_path),
            "--confirmed-selection-by-user", "--db-path", str(self.db_path)
        )
        self.assertNotEqual(0, added.returncode)
        self.assertFalse((self.root / "attachments").exists())
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0])
```

Add CLI coverage for `db attach <item_id>` to a text-only existing question
and for a duplicate whose role conflicts with an existing attachment.

- [ ] **Step 2: Run import tests and observe failure**

Run:

```powershell
& '<python>' -m unittest tests.test_export_review_set.AttachmentImportTests -v
```

Expected: failures because files are not copied, metadata is not returned, and
`db attach` is not recognized.

- [ ] **Step 3: Implement attachment storage, metadata loading, and `db attach`**

Add focused helpers:

```python
def attachment_destination(context: StorageContext, question_id: str, verified: dict[str, Any]) -> tuple[Path, str]:
    stem = clean_filename(verified["source"].stem, "image", 60)
    relative = Path("attachments") / question_id / f"{stem}-{verified['sha256']}{verified['extension']}"
    return context.notes_root / relative, relative.as_posix()


def copy_managed_attachment(context: StorageContext, question_id: str, verified: dict[str, Any], created_files: list[Path]) -> str:
    final_path, relative = attachment_destination(context, question_id, verified)
    if final_path.exists():
        return relative
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = final_path.parent / f".{final_path.name}.{uuid.uuid4().hex}.tmp"
    shutil.copyfile(verified["source"], temporary)
    temporary.replace(final_path)
    created_files.append(final_path)
    return relative
```

Implement `insert_attachment()` with conflict detection on
`(question_id, sha256)`, `load_attachments()` using a second query for returned
question IDs, and attachment serialization with the absolute managed path and
`missing` flag. Change `db_add()` to use `resolve_storage_context()`, validate
all source images before any write, track newly copied files, and roll them
back on any exception. Add `db attach` parser and command using the same
verified-copy/row-insert behavior.

- [ ] **Step 4: Run attachment import tests and existing import regressions**

Run:

```powershell
& '<python>' -m unittest tests.test_export_review_set.AttachmentImportTests tests.test_export_review_set.AddQuestionSelectionTests -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit managed-file import support**

```powershell
git add scripts/export_review_set.py tests/test_export_review_set.py
git commit -m "feat: import and attach managed question images"
```

### Task 3: Enforce Role-Aware Query And Export Visibility

**Files:**
- Modify: `tests/test_export_review_set.py`
- Modify: `scripts/export_review_set.py`

- [ ] **Step 1: Write failing role-visibility and export-integrity tests**

Seed one question with both prompt and solution PNG images, then assert:

```python
class AttachmentVisibilityTests(unittest.TestCase):
    def test_quiz_returns_prompt_attachment_without_solution_attachment(self):
        quiz = json.loads(self.run_cli("db", "quiz", "--course", "Images", "--db-path", str(self.db_path)).stdout)
        attachments = quiz["items"][0]["attachments"]
        self.assertEqual(["prompt"], [item["role"] for item in attachments])

    def test_questions_only_exports_only_prompt_image(self):
        content = self.export_content("questions-only")
        self.assertIn("prompt", content)
        self.assertNotIn("solution", content)

    def test_full_export_places_solution_image_after_answer_heading(self):
        content = self.export_content("full")
        self.assertLess(content.index("prompt"), content.index("### 回答"))
        self.assertGreater(content.index("solution"), content.index("### 回答"))

    def test_export_rejects_tampered_managed_image(self):
        Path(self.solution_managed_path).write_bytes(b"modified")
        exported = self.run_cli(
            "db", "export", "--course", "Images", "--mode", "full",
            "--db-path", str(self.db_path)
        )
        self.assertNotEqual(0, exported.returncode)
        self.assertIn("differs from stored digest", exported.stderr)
```

Also assert `db due` does not expose solution attachments and text-only export
output remains identical to its current expected Markdown.

- [ ] **Step 2: Run visibility tests and observe failure**

Run:

```powershell
& '<python>' -m unittest tests.test_export_review_set.AttachmentVisibilityTests -v
```

Expected: failures because output has no role-aware attachment rendering.

- [ ] **Step 3: Implement attachment-aware result assembly and Markdown rendering**

Extend question and quiz serialization without changing text-only output:

```python
def add_attachments_to_items(items: list[dict[str, Any]], attachments: dict[str, list[dict[str, Any]]], role: str | None = None) -> None:
    for item in items:
        visible = attachments.get(item["id"], [])
        if role:
            visible = [attachment for attachment in visible if attachment["role"] == role]
        if visible:
            item["attachments"] = visible


def render_markdown_attachment(attachment: dict[str, Any]) -> str:
    caption = attachment["caption"].replace("[", r"\[").replace("]", r"\]")
    relative = Path("..") / Path(attachment["stored_relative_path"])
    return f"![{caption}]({relative.as_posix()})"
```

For `db quiz` and `db due`, attach only `prompt` metadata. For search and
pending results, include both roles and extend rendered content around the
existing question/answer sections. Before Markdown export, verify every
selected managed file still exists and its SHA-256 matches the stored digest.
Render prompt images after `### 问题`; render solution images only after
`### 回答` in `full` mode.

- [ ] **Step 4: Run visibility, export, and text-only regression tests**

Run:

```powershell
& '<python>' -m unittest tests.test_export_review_set.AttachmentVisibilityTests tests.test_export_review_set.ExportQuestionsTests -v
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit visibility support**

```powershell
git add scripts/export_review_set.py tests/test_export_review_set.py
git commit -m "feat: render image attachments by review role"
```

### Task 4: Add Confirmed Metadata Changes, Detach, Delete, And Audit

**Files:**
- Modify: `tests/test_export_review_set.py`
- Modify: `scripts/export_review_set.py`

- [ ] **Step 1: Write failing destructive-safety and audit tests**

Add tests that assert authorization and file preservation:

```python
class AttachmentMaintenanceTests(unittest.TestCase):
    def test_attachment_update_requires_confirmation_before_solution_becomes_prompt(self):
        updated = self.run_cli(
            "db", "attachment-update", str(self.solution_id), "--role", "prompt",
            "--db-path", str(self.db_path)
        )
        self.assertNotEqual(0, updated.returncode)
        self.assertIn("explicit user confirmation", updated.stderr)

    def test_detach_requires_confirmation_and_preserves_unrelated_file(self):
        unrelated = self.question_dir / "keep-me.txt"
        unrelated.write_text("unmanaged", encoding="utf-8")
        rejected = self.run_cli("db", "detach", str(self.prompt_id), "--db-path", str(self.db_path))
        self.assertNotEqual(0, rejected.returncode)
        detached = self.run_cli(
            "db", "detach", str(self.prompt_id), "--confirmed-by-user", "--db-path", str(self.db_path)
        )
        self.assertEqual(0, detached.returncode, detached.stderr)
        self.assertTrue(unrelated.exists())

    def test_delete_requires_confirmation_and_removes_only_referenced_images(self):
        rejected = self.run_cli("db", "delete", self.item_id, "--db-path", str(self.db_path))
        self.assertNotEqual(0, rejected.returncode)
        deleted = self.run_cli(
            "db", "delete", self.item_id, "--confirmed-by-user", "--db-path", str(self.db_path)
        )
        self.assertEqual(0, deleted.returncode, deleted.stderr)
        self.assertTrue(self.unrelated_path.exists())

    def test_audit_reports_orphan_modified_and_missing_files_without_mutating(self):
        report = json.loads(self.run_cli("db", "attachment-audit", "--db-path", str(self.db_path)).stdout)
        self.assertEqual(1, len(report["missing_files"]))
        self.assertEqual(1, len(report["modified_files"]))
        self.assertEqual(1, len(report["orphan_files"]))
        self.assertTrue(self.orphan_path.exists())
```

- [ ] **Step 2: Run maintenance tests and observe failure**

Run:

```powershell
& '<python>' -m unittest tests.test_export_review_set.AttachmentMaintenanceTests tests.test_export_review_set.DeleteQuestionTests -v
```

Expected: failures because new commands and confirmed file deletion behavior
are not available.

- [ ] **Step 3: Implement confirmed maintenance commands and trash-staged deletes**

Implement safe path and trash helpers:

```python
def managed_attachment_path(context: StorageContext, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"Unsafe managed attachment path: {relative}")
    root = context.attachments_root.resolve()
    resolved = (context.notes_root / relative_path).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Managed attachment escapes attachments root: {relative}")
    return resolved


def stage_managed_files(context: StorageContext, paths: list[Path]) -> tuple[Path, list[tuple[Path, Path]]]:
    trash = context.attachments_root / ".trash" / uuid.uuid4().hex
    moves: list[tuple[Path, Path]] = []
    for source in paths:
        if source.exists():
            destination = trash / source.relative_to(context.attachments_root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            moves.append((source, destination))
    return trash, moves
```

Add `db attachment-update`, requiring `--confirmed-by-user` and at least one
metadata change. Add `db detach`, requiring confirmation, staging its single
referenced file in trash before deleting the row, restoring it on rollback,
and deleting trash after commit. Tighten `db delete` to require confirmation
and apply the same flow to referenced attachment files only. Add
`db attachment-audit`, which reports missing/digest-mismatched/referenced,
orphan, and trash files; prune only with explicit cleanup flags plus
confirmation.

- [ ] **Step 4: Run maintenance and deletion regression tests**

Run:

```powershell
& '<python>' -m unittest tests.test_export_review_set.AttachmentMaintenanceTests tests.test_export_review_set.DeleteQuestionTests -v
```

Expected: all selected tests pass after updating existing delete tests to
include confirmed deletion where the delete is intended to succeed.

- [ ] **Step 5: Commit maintenance safety support**

```powershell
git add scripts/export_review_set.py tests/test_export_review_set.py
git commit -m "feat: manage attachment deletion and audit safely"
```

### Task 5: Document The Public Workflow And Verify Complete Behavior

**Files:**
- Modify: `references/schema.md`
- Modify: `SKILL.md`
- Modify: `tests/test_export_review_set.py`

- [ ] **Step 1: Write failing instruction and schema assertions**

Extend instruction tests:

```python
class AttachmentInstructionsTests(unittest.TestCase):
    def test_skill_documents_safe_optional_image_workflow(self):
        instructions = SKILL.read_text(encoding="utf-8")
        self.assertIn("Attachment Decision Rule", instructions)
        self.assertIn("`prompt`", instructions)
        self.assertIn("`solution`", instructions)
        self.assertIn("Solution images must not appear in `db quiz`, `db due`, or `questions-only` exports", instructions)
        self.assertIn("db detach <attachment_id> --confirmed-by-user", instructions)
        self.assertIn("db delete <item_id> --confirmed-by-user", instructions)

    def test_schema_documents_attachment_import_fields(self):
        schema = (SKILL.parent / "references" / "schema.md").read_text(encoding="utf-8")
        for token in ("attachments", "source_path", "role", "provenance", "caption"):
            self.assertIn(token, schema)
```

- [ ] **Step 2: Run instruction tests and observe failure**

Run:

```powershell
& '<python>' -m unittest tests.test_export_review_set.AttachmentInstructionsTests -v
```

Expected: failures because the existing skill and schema reference do not
document image attachments.

- [ ] **Step 3: Update skill workflow and schema reference**

In `references/schema.md`, document this optional object:

```json
"attachments": [
  {
    "source_path": "<source-image-path>",
    "role": "prompt",
    "provenance": "provided",
    "caption": "Network layout used by the question"
  }
]
```

State that `role` is `prompt` or `solution`, `provenance` is `provided` or
`reconstructed`, and all four values are required for each attachment.

In `SKILL.md`, add:

```markdown
## Optional Image Attachments

### Attachment Decision Rule

Attach an image only when its visual information is necessary to solve or
understand the question, or when the user explicitly requests preservation.
Classify question-visible images as `prompt` and answer-revealing images as
`solution`. A reconstructed image must use `provenance` value `reconstructed`.

Solution images must not appear in `db quiz`, `db due`, or `questions-only`
exports. Use `full` export or a content-inclusive search only when answer
material is appropriate.

Use explicit authorization before metadata changes or deletion:

`db attachment-update <attachment_id> ... --confirmed-by-user`
`db detach <attachment_id> --confirmed-by-user`
`db delete <item_id> --confirmed-by-user`
```

Update existing deletion command examples to include
`--confirmed-by-user`.

- [ ] **Step 4: Run the complete automated suite**

Run:

```powershell
& '<python>' -m unittest discover -s tests -v
```

Expected: all tests pass with no tracebacks or warnings.

- [ ] **Step 5: Commit documentation and full regression coverage**

```powershell
git add SKILL.md references/schema.md tests/test_export_review_set.py
git commit -m "docs: describe image attachment study workflow"
```

### Task 6: Backfill An Existing Diagram Question And Perform Manual Acceptance Checks

**Files:**
- Create only if reconstruction is needed: a local prompt-image asset under the configured notes root before `db attach`
- Modify through CLI only: configured `exercise_bank.sqlite3` and its managed `attachments/` directory

- [ ] **Step 1: Find the exact saved item and confirm no existing prompt attachment**

Run:

```powershell
& '<python>' '<script>' db search --query '<question keyword>' --course '<course>' --limit 3 --include-content
```

Expected: one matching item with an ID to use below and no prompt attachment,
or a matching stored ID to use in the following commands if existing data differs.

- [ ] **Step 2: Attach the available original figure or an accurately labelled reconstruction**

For a source image available from the user:

```powershell
& '<python>' '<script>' db attach <item_id> --source '<source-path>' --role prompt --provenance provided --caption 'Diagram shown in the question'
```

For a locally recreated image based on the known problem statement:

```powershell
& '<python>' '<script>' db attach <item_id> --source '<reconstructed-path>' --role prompt --provenance reconstructed --caption 'Reconstructed diagram for the question'
```

Expected: one managed prompt attachment is returned for the saved item.

- [ ] **Step 3: Verify quiz, export, and audit behavior on the configured bank**

Run:

```powershell
& '<python>' '<script>' db quiz --course '<course>' --limit 3
& '<python>' '<script>' db export --course '<course>' --mode questions-only
& '<python>' '<script>' db attachment-audit
```

Expected: quiz metadata and Markdown include the prompt image; the audit
reports no missing, modified, orphaned, or trash files.

- [ ] **Step 4: Commit the approved design and implementation plan with the completed feature branch**

```powershell
git add docs/superpowers/specs/2026-05-24-question-image-attachments-design.md docs/superpowers/plans/2026-05-24-question-image-attachments.md
git commit -m "docs: record question image attachment design and plan"
```

The production database and attachment files are user study data and are not
committed to the skill repository.

## Plan Self-Review

- Every accepted design requirement is mapped to a task: role visibility
  (Task 3), verified managed storage and rollback (Tasks 1-2), confirmed
  destructive operations and audit (Task 4), public workflow documentation
  (Task 5), and the requested first backfill (Task 6).
- The implementation remains inside the existing CLI script and adjacent
  docs/tests, avoiding an unrelated restructure or generalized media system.
- Each production-code behavior is preceded by a failing automated test, and
  the configured study database is touched only after automated verification.
