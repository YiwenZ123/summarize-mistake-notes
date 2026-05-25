# Optional Image Attachments for Mistake Notes

Date: 2026-05-24
Status: Revised design for review

## Goal

Extend the SQLite exercise bank so a saved question can retain essential image
attachments without weakening the current text-only workflow or revealing
answer material during self-testing.

A representative acceptance case is an existing diagram-based question that
needs its original prompt figure restored for self-testing.

## Scope

This increment supports:

- image attachments stored as managed files beside the SQLite database;
- explicit distinction between question-facing and answer-facing images;
- import-time attachments and attachment maintenance commands for existing
  questions;
- safe image display in search, quiz, due-review, and Markdown export flows;
- safe cleanup, rollback, and integrity reporting for managed image files.

This increment does not implement:

- OCR or image-text indexing;
- arbitrary document, audio, or video attachments;
- image annotation, editing, or thumbnail generation;
- automatic migration of images that may be embedded in old Markdown files.

The existing textual question fields remain the searchable and quiz-generating
source of truth. Images supplement them where visual information is material.

## Attachment Decision Rule

Save an image when it carries information that materially affects
understanding or solving the problem:

- network topology, geometry, force diagrams, process diagrams, or labelled maps;
- chart readings, plotted relationships, tables, or spatial annotations;
- source layout or symbols that cannot be transcribed reliably into plain text;
- an explicit user request to preserve the question or solution image.

Do not save an image when the question can be completely and unambiguously
represented in text, or when an image is merely decorative.

If an essential uploaded image is not available as a readable local file,
prefer a faithful local rendering reconstructed from supplied content. A
reconstruction must be recorded as `reconstructed`; it must never be described
as the preserved source image. Ask for the original file only when fidelity
beyond a reconstruction matters.

## Attachment Roles And Exposure

Every saved image has a required `role`:

- `prompt`: material the learner may see before answering, such as a network
  diagram, problem table, or original question screenshot.
- `solution`: material that may reveal an answer, derivation, grading mark, or
  completed working.

Role is deliberately explicit rather than inferred from the caption. An image
with an unknown role must be rejected rather than accidentally exposed in a
quiz.

| Operation | `prompt` image | `solution` image |
|---|---:|---:|
| `db quiz` / `db due` | Include | Omit |
| `db export --mode questions-only` | Include under `### 问题` | Omit |
| `db export --mode full` | Include under `### 问题` | Include under `### 回答` |
| `db search` / `db pending` | Include metadata | Include metadata |
| `db search --include-content` / `db pending --include-content` | Render after question | Render after answer |

`db search` and `db pending` already expose saved answer content and are not
self-test interfaces. The skill must continue to use `db quiz` or `db due`
when quizzing a learner.

## Storage Model

Images are copied into the managed notes root rather than stored as SQLite
BLOB data:

```text
<notes_root>/
  exercise_bank.sqlite3
  attachments/
    <question_id>/
      <sanitized-stem>-<sha256>.<canonical-extension>
    .trash/
      <operation-id>/
        ...
  exports/
```

The database stores relative managed paths so a user can move the complete
notes directory without rewriting attachment rows.

### Storage Context Resolution

All commands that add, read, render, detach, or delete attachments use one
shared `resolve_storage_context()` rule:

1. If `--db-path` is provided, use that file as the database path; otherwise
   use the configured database path or `<configured-notes-root>/exercise_bank.sqlite3`.
2. If `--notes-root` is provided, use it as the managed root.
3. Otherwise, if `--db-path` is provided, use the database file's parent
   directory as the managed root.
4. Otherwise, use the configured notes root.

Commands requiring this shared resolution are:

```text
db add
db attach
db attachment-update
db detach
db delete
db search
db pending
db quiz
db due
db export
db attachment-audit
```

This keeps normal operation portable and ensures isolated tests that use only
`--db-path` keep their attachments beside the test database.

### Managed Path Rules

- `stored_relative_path` uses forward-slash relative paths such as
  `attachments/<item_id>/question-figure-<sha256>.png`.
- It must not be absolute or contain `..`.
- Before creating, reading, moving, or deleting a managed file, reject any
  existing symbolic link or Windows junction in `attachments/`, its question
  directory, or the target parent chain.
- A path resolving through a symbolic link or junction is reported by audit as
  `unsafe_paths`; it must not be opened, rendered, moved, or deleted.
- After explicit authorization, detach or question deletion may remove the
  database association for an unsafe attachment and report
  `file_cleanup_skipped`, leaving the external file untouched.
- `original_filename` is metadata only and is never used as a filesystem path.

## Database Schema

Add a table and retrieval index through `ensure_schema()`:

```sql
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
```

`UNIQUE(question_id, sha256)` means identical bytes are stored only once per
question. If the same image is supplied again with a conflicting role,
caption, or provenance, the command reports the existing attachment and asks
the caller to use the explicit metadata-update command. It must not silently
change whether an image appears in quizzes.

## Supported Image Validation

Supported decoded formats are PNG, JPEG, and WebP. Output files use a
canonical extension derived from decoded content: `.png`, `.jpg`, or `.webp`.
The input filename suffix is not trusted.

The stored filename uses the complete SHA-256 digest rather than a truncated
prefix, preventing two distinct files with a shared short prefix from mapping
to the same destination name.

For `db validate`, inspect the supplied source without writing files:

1. Confirm the source path exists and resolves to a regular readable file.
2. Reject a source file larger than 25 MiB.
3. Open and verify the image with Pillow (`PIL.Image.verify()`), which is
   available in the bundled runtime.
4. Reject decompression-bomb warnings or errors and any decoded format outside
   PNG, JPEG, or WebP.
5. Compute the SHA-256 digest from the verified source bytes.

For `db add` and `db attach`, validation is performed again on an immutable
working snapshot: stream-copy the source into a bounded temporary managed
file while hashing it, reject more than 25 MiB during copying, verify that
temporary file with Pillow, and install only that verified snapshot. This
prevents a source replacement between validation and copying from entering
the managed attachment store.

## Import JSON Interface

Existing JSON imports without attachments remain valid. An item with an
attachment uses the following optional field:

```json
{
  "attachments": [
    {
      "source_path": "<source-image-path>",
      "role": "prompt",
      "provenance": "provided",
      "caption": "Diagram shown in the question"
    }
  ]
}
```

For each attachment object:

- `source_path`, `role`, `provenance`, and non-empty `caption` are required;
- `role` is only `prompt` or `solution`;
- `provenance` is only `provided` or `reconstructed`;
- `db validate` validates the attachment object and verifies the referenced
  local image, but does not create directories, copy images, or write rows.
- `db add` and `db attach` repeat all source-image verification immediately
  before copying; a preceding `db validate` result is not trusted after a
  source file could have changed.

### Import And Update Semantics

- New item with no `attachments`: create a text-only question exactly as now.
- New item with attachments: write the question and all validated attachments
  as one command-level unit of work.
- Existing item updated with no `attachments`: preserve existing attachments.
- Existing item updated with an empty `attachments` list: also preserve
  existing attachments; omission or emptiness never means deletion.
- Existing item updated with non-empty `attachments`: add validated files that
  are not already attached to the question.
- Existing attachment bytes are not copied again. Conflicting metadata is
  reported and only changed through `db attachment-update`.
- Import never removes images. Removal requires an explicit destructive
  maintenance command.

## Maintenance Command Interface

Add the following commands:

```text
db attach <item_id> --source "<image-path>" --role <prompt|solution> \
  --provenance <provided|reconstructed> --caption "<text>" \
  [--notes-root "<path>"] [--db-path "<path>"]

db attachment-update <attachment_id> [--role <prompt|solution>] \
  [--provenance <provided|reconstructed>] [--caption "<text>"] \
  --confirmed-by-user [--notes-root "<path>"] [--db-path "<path>"]

db detach <attachment_id> --confirmed-by-user \
  [--notes-root "<path>"] [--db-path "<path>"]

db delete <item_id> --confirmed-by-user \
  [--notes-root "<path>"] [--db-path "<path>"]

db attachment-audit [--prune-orphans] [--empty-trash] [--confirmed-by-user] \
  [--notes-root "<path>"] [--db-path "<path>"]
```

Rules:

- `db attach` backfills an existing question without reconstructing question
  JSON.
- `db attachment-update` is required for changing role, provenance, or
  caption, requires at least one changed metadata option, and requires
  confirmation because changing `solution` to `prompt` changes what a learner
  can see before answering.
- `db detach` removes one attachment row and its one managed file only after
  explicit user authorization.
- `db delete` now requires explicit confirmation because deletion can remove
  managed files as well as database records and review events.
- `db attachment-audit` reports missing managed files, unmanaged orphan files,
  modified-content digest mismatches, leftover trash, and unsafe linked paths.
  It is read-only unless at least one cleanup flag and the single
  `--confirmed-by-user` switch are both provided; unsafe paths are never
  followed for cleanup.

The skill instructions must be updated so Codex requests explicit user
selection before adding question attachments and explicit authorization before
attachment metadata changes, detach, or question deletion.

## Addition Transaction And Recovery

SQLite transactions and filesystem operations cannot be committed atomically
together. The implementation therefore favors never committing a database
attachment row that points to a file it has not yet created.

For `db add` and `db attach`:

1. Resolve the storage context.
2. Validate input fields and preflight source images before making any
   managed-file change.
3. Start one `BEGIN IMMEDIATE` SQLite transaction for the complete command so
   concurrent attachment writes are serialized.
4. For every requested attachment, stream-copy to a unique temporary file
   inside its validated `attachments/<question_id>/` directory, enforcing the
   size limit and computing the digest while copying.
5. Verify that snapshot with Pillow, determine whether it is already linked
   by SHA-256 and question ID, then atomically install only new verified files
   at their canonical filenames. Track every final file newly created by this
   command.
6. Insert or update question rows and insert attachment rows within the same
   SQLite transaction.
7. Commit the SQLite transaction only after all final managed files exist.
8. On any handled validation, copy, SQL, or commit failure, roll back the
   SQLite transaction and delete every temporary or final file created by this
   invocation; never delete files that existed before the invocation.

A process or machine crash after final-file creation but before SQLite commit
can leave an unreferenced managed file. `db attachment-audit` detects that
orphan and can remove it after explicit user confirmation. This is preferable
to a committed quiz item referencing a missing essential image.

## Detach And Delete Safety

Destructive operations use a reversible staging step:

1. Resolve and validate only the managed paths referenced by the target
   attachment rows.
2. For each existing referenced file, atomically move it under
   `attachments/.trash/<operation-id>/` on the same managed root.
3. In one SQLite transaction, delete the selected attachment row, or delete
   the question and rely on foreign-key cascade for its attachment rows.
4. Commit the SQLite transaction.
5. After commit, remove that operation's trash directory and remove now-empty
   question directories.

If the database transaction fails, restore moved files from trash before
reporting failure. If database deletion commits but final trash cleanup fails,
report success with a cleanup warning and leave the trash entry for
`db attachment-audit --empty-trash --confirmed-by-user`.

If a referenced file was already missing before a confirmed detach or delete,
the command may remove the database row but must report the missing-file
condition in its result.

Only referenced managed files may be moved or deleted. Deleting a question
must not recursively remove unknown files found in its directory; the audit
command handles unreferenced content explicitly.

## Query And Rendering Data Flow

Base question queries must continue selecting and ordering question rows as
they do now. To avoid row multiplication and incorrect limits, attachment
retrieval uses a second query keyed by the returned question IDs rather than a
join in the limited question query.

Attachment metadata returned to callers contains:

```json
{
  "id": 1,
  "role": "prompt",
  "provenance": "provided",
  "caption": "Diagram shown in the question",
  "stored_relative_path": "attachments/<item_id>/question-figure-<sha256>.png",
  "managed_path": "<notes_root>\\attachments\\<item_id>\\question-figure-<sha256>.png",
  "media_type": "image/png",
  "byte_size": 12345,
  "missing": false
}
```

For interactive output:

- `db quiz` and `db due` return only `prompt` attachment metadata.
- `db search` and `db pending` may return both roles because they already
  expose stored solution content; rendered content places prompt images after
  the question and solution images after the answer.
- A missing managed file is returned with `missing: true` so the user can
  repair or detach it.
- A text-only question omits attachment metadata entirely, preserving existing
  JSON result shapes unless the question actually has images.

For Markdown exports:

- `questions-only` writes only prompt images beneath `### 问题`.
- `full` writes prompt images beneath `### 问题` and solution images beneath
  `### 回答`.
- References are relative to `exports/`, for example:

  ```markdown
  ![Diagram shown in the question](../attachments/<item_id>/question-figure-<sha256>.png)
  ```

- Captions are Markdown image alt text and must be escaped sufficiently to
  preserve valid link syntax.
- Export fails with a clear missing-or-modified attachment error rather than
  writing a review sheet with a broken or untrusted essential image link.
- Text-only question query and export output remains byte-for-byte unchanged.

## Compatibility And Migration

- `ensure_schema()` creates `question_attachments` and its index if absent.
  Existing `questions`, `courses`, and `review_events` rows are not rewritten.
- Existing JSON without attachments, normal text searches, text-only exports,
  and review-state changes remain valid.
- `db add`, display commands, export, and destructive commands gain consistent
  optional `--notes-root` support where attachment paths may be needed.
- The `db delete` confirmation flag is an intentional safety tightening; the
  skill instructions and existing deletion tests must be updated together.
- Markdown export remains a sharing/backup format; SQLite remains the primary
  store for attachments and review state.

## Existing Question Backfill

An already saved diagram-based question may need a prompt figure when its
visual relationships or labels are material to solving it.

After implementation:

1. Obtain a readable local image containing the material visual information
   needed for the stored question.
2. Use `--provenance provided` when copying an available source image. If the
   image is recreated from supplied content, use `--provenance reconstructed`
   and a caption that states it is reconstructed.
3. Attach a copied source image with:

   ```text
   db attach <item_id> --source "<source-image-path>" --role prompt --provenance provided --caption "Diagram shown in the question"
   ```

4. Verify that `db quiz` exposes the prompt image, `questions-only` export
   embeds it beneath the question, and `db attachment-audit` reports no
   missing or orphaned files.

## Implementation Boundaries

The implementation should keep responsibilities focused within the existing
script and its adjacent documentation/tests:

- `scripts/export_review_set.py`: schema creation, storage-context helper,
  attachment validation/copy/delete/audit helpers, CLI commands, result
  assembly, and Markdown rendering.
- `references/schema.md`: optional attachment JSON schema, attachment roles,
  provenance values, and validation requirements.
- `SKILL.md`: user authorization rules, image decision rule, quiz exposure
  rules, and new maintenance command usage.
- `tests/test_export_review_set.py`: CLI behavior, filesystem safety,
  compatibility, export visibility, and instruction assertions.

Do not introduce OCR, a separate media service, or generalized binary
attachment abstractions in this increment.

## Required Tests

Add automated tests before production changes for:

### Backward Compatibility

- a text-only import, search, quiz, and export remain unchanged;
- an existing database is migrated by adding the new table without changing
  existing question or review-history rows;
- an update with omitted or empty `attachments` preserves previously linked
  files.

### Validation And Storage

- a valid prompt image import copies the verified image under
  `attachments/<question_id>/` and returns attachment metadata;
- JPEG and WebP input use canonical stored extensions;
- a missing file, a file above the size limit, a corrupt image, and a
  non-image renamed to `.png` are rejected without database rows or copied
  files;
- a reconstructed attachment must record `provenance: reconstructed`;
- duplicate image import or `db attach` does not create a second copy;
- a duplicate with conflicting role or provenance requires
  `db attachment-update`.

### Root Resolution And Paths

- each attachment-aware command with only `--db-path` uses the database parent
  as the notes root;
- an explicit `--notes-root` correctly supports a database tree moved as a
  unit;
- traversal-like stored paths and symlink escapes are refused for read or
  delete operations.

### Transaction And Destructive Safety

- if the second attachment in one `db add` fails, no question rows, attachment
  rows, or newly copied files from that command remain;
- `db attach` failure after file creation cleans up its newly created file;
- `db detach` requires confirmation, removes only its referenced managed
  image, and preserves unrelated or orphan files;
- `db delete` requires confirmation, removes database rows and referenced
  managed files, and preserves unrelated files;
- simulated post-commit trash cleanup failure returns a warning and is
  recoverable by confirmed audit cleanup;
- `db attachment-audit` reports missing files, modified digest mismatches,
  orphan files, and trash without deleting anything unless confirmed cleanup
  was requested.

### Visibility And Export

- `db quiz` and `db due` return prompt images but never solution images;
- `questions-only` export contains prompt images but never solution images;
- `full` export places prompt and solution images in their corresponding
  sections;
- exports for text-only questions contain no extra attachment formatting;
- exports fail clearly if a linked managed image is missing or differs from its
  stored digest;
- `db search --include-content` renders both roles in the appropriate content
  section.

### Skill Instructions

- skill instructions contain the optional-image decision rule;
- skill instructions require role-aware attachment handling and keep solution
  images out of quizzes;
- skill instructions require user confirmation before attachment metadata
  updates, detach, and question deletion.

## Acceptance Criteria

The feature is complete when:

- text-only users see no behavioral change except the safer confirmed delete
  command;
- a question can hold verified prompt and solution images without storing
  binary data in SQLite;
- quiz and questions-only flows cannot reveal solution-role images;
- failed multi-item operations do not leave ordinary orphan files;
- unexpected crash residue is detectable and removable only after explicit
  authorization;
- moved database directories, local tests, exports, and destructive commands
  all resolve the same managed attachment root;
- An existing diagram-based question can be backfilled with a prompt image and successfully reviewed
  through quiz and export flows.
