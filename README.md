# summarize-mistake-notes

[简体中文](README.zh-CN.md)

`summarize-mistake-notes` is an agent-neutral `SKILL.md` skill for turning study questions, corrections, and mistake notes into a structured exercise bank. It keeps searchable content and a spaced-repetition review schedule in SQLite, while supporting guided self-testing, scoped Markdown exports, and optional image attachments for questions that depend on visual information.

## Capabilities

| Capability | What it provides |
| --- | --- |
| SQLite source of truth | Questions, classifications, answers, and review state live in one exercise database rather than scattered Markdown state files. |
| Spaced-repetition review | Quiz and due-review flows expose prompts without answer content. Each answer is graded `correct`, `partial`, or `wrong`, which schedules the next review after 1, 3, 7, 14, and 30 days. |
| Scoped Markdown exports | Create shareable full review sheets or `questions-only` self-test sheets for a selected course or topic. |
| Managed image attachments | Preserve essential `prompt` or `solution` images as managed files while SQLite stores their metadata. |
| Safety and integrity | Explicit save, edit, reset, and deletion confirmations, image verification, SHA-256 integrity metadata, and attachment auditing protect local study material. A moved or still-syncing notes folder is reported instead of being replaced by an empty database. |

## How It Works

1. The agent gathers candidate review items and obtains the learner's explicit selection before saving.
2. Structured JSON is validated and then written through `scripts/export_review_set.py` into `<notes_root>/exercise_bank.sqlite3`.
3. Search, quiz, due-review, and grading commands read and update the SQLite exercise bank; every graded answer schedules that item's next review.
4. Markdown exports are generated only for sharing, backup, or self-testing; they are not the review-state store.
5. Essential images may be copied under `<notes_root>/attachments/`, with role-aware display rules that keep solution material out of self-tests.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `SKILL.md` | Operational instructions followed by the agent during save, search, review, export, and attachment tasks. |
| `scripts/export_review_set.py` | SQLite, review scheduling, export, attachment-management, and CLI implementation. |
| `references/schema.md` | JSON import and update contract, including optional attachment fields. |
| `tests/` | Behavior, safety, scheduling, and documentation-consistency tests for the CLI workflow. |
| `.github/workflows/tests.yml` | Runs the test suite on Linux, macOS, and Windows. |
| `docs/superpowers/specs/2026-05-24-question-image-attachments-design.md` | Design rationale and safety contract for managed images. |

## Requirements

- An agent environment that supports local `SKILL.md` skills.
- Python 3.9 or newer. SQLite is provided by Python's standard library. Pillow is needed only for image attachments; without it, attachment commands report `PILLOW_REQUIRED` and every other command keeps working.
- Git for cloning, contribution, and repository validation workflows.

## Installation

Clone this repository and place the resulting folder in any skill directory supported by your agent. The repository uses the common `SKILL.md` layout and does not provide an installer.

```bash
git clone https://github.com/YiwenZ123/summarize-mistake-notes.git summarize-mistake-notes
cd summarize-mistake-notes
python3 scripts/export_review_set.py --help
```

The examples use `python3`; replace it with the appropriate Python 3 command for your system. Agents should resolve `scripts/export_review_set.py` relative to the installed `SKILL.md`, not the user's current workspace.

## Configure Storage

No notes root is shipped or preconfigured with this repository. When the agent first needs database access, it checks the configuration; if no location is set, it asks the learner which private folder should store the exercise database. Once selected, the database is stored at `<notes_root>/exercise_bank.sqlite3`, managed images at `<notes_root>/attachments/`, and generated Markdown exports at `<notes_root>/exports/`.

The default CLI config file is `~/.config/summarize-mistake-notes/config.json`. Use `--config` to select a different config file.

```bash
python3 scripts/export_review_set.py config get
python3 scripts/export_review_set.py config set --notes-root "<folder chosen by the user>"
```

If the configured folder later becomes unavailable (moved, renamed, disconnected, or a cloud folder that has not finished syncing), every command stops with `NOTES_ROOT_MISSING`. A folder that still contains exercise-bank files but no database stops with `NOTES_DB_MISSING`. The CLI never creates a replacement in either case, and read-only commands never create a database file; only the first `db add` does.

Opening an exercise bank created by an earlier version upgrades its schema in place and keeps every item and its review history. A database created by a newer version of the skill is refused rather than modified.

Never hand-edit the SQLite database. Create, search, review, export, update, and delete content through the script.

## Typical Workflows

Run the following examples from the skill directory with Python 3.9 or newer; image commands also need Pillow.

### Validate And Save Selected Items

Prepare JSON in the format described below. The `--confirmed-selection-by-user` flag must be used only after the learner explicitly selected which candidate items to save. Include an essential image in an import only after the learner explicitly requests or approves it.

```bash
python3 scripts/export_review_set.py db validate --input "<prepared-json-file>"
python3 scripts/export_review_set.py db add --input "<prepared-json-file>" --confirmed-selection-by-user
```

### Inspect And Search

```bash
python3 scripts/export_review_set.py db stats --human --topics
python3 scripts/export_review_set.py db search --query "shortest path" --limit 10 --include-content
python3 scripts/export_review_set.py db search --course "Transport" --topic "Equilibrium" --limit 10
```

`--query` searches all saved text, while `--course` and `--topic` match name substrings.

### Review And Record Results

`db due` lists items scheduled for today or earlier, most overdue first; `--shuffle` randomizes the order. `db quiz` targets a course, topic, or keyword and also offers items that are not due yet for practice. Neither command reveals answers. After the learner answers, `db show` returns the saved answer and review history for grading, and `db grade` records the result:

```bash
python3 scripts/export_review_set.py db due --limit 3
python3 scripts/export_review_set.py db quiz --topic "Network Equilibrium" --limit 3
python3 scripts/export_review_set.py db show <item_id>
python3 scripts/export_review_set.py db grade <item_id> --result partial --note "Missed the capacity constraint."
```

| Result | Effect on the schedule |
| --- | --- |
| `correct` on a due item | Moves to the next interval (1, 3, 7, 14, then 30 days); the item is mastered after the 30-day review. |
| `correct` on an item that is not due | Recorded as practice; the schedule does not change. |
| `partial` | Keeps the current interval and returns tomorrow. |
| `wrong` | Restarts the schedule and returns tomorrow. |

`db mark-done <item_id>` masters an item immediately, and `db mark-wrong <item_id>` is a shortcut for `db grade --result wrong`. To revise a whole course again, `db reset --course "<course>"` previews the items that would restart and `--confirmed-by-user` applies it; `db quiz --include-done` practices mastered items without resetting them.

### Edit A Saved Question

`db update` changes fields of one saved item in place and keeps its id, attachments, and review history. Without `--confirmed-by-user` it only prints a preview of the changes. See [references/schema.md](references/schema.md) for the editable fields.

```bash
python3 scripts/export_review_set.py db update <item_id> --input "<changes-json-file>"
python3 scripts/export_review_set.py db update <item_id> --input "<changes-json-file>" --confirmed-by-user
```

### Export A Scoped Review Sheet

An export must be scoped by `--course`, `--topic`, or both. Use `full` when answers are appropriate and `questions-only` for self-testing.

```bash
python3 scripts/export_review_set.py db export --course "Transport Planning" --mode full
python3 scripts/export_review_set.py db export --topic "Network Equilibrium" --mode questions-only
```

### Attach And Audit Images

Use an attachment only when an image is essential to understanding or solving the saved question, or when the learner explicitly requests preservation. Attach it only after the learner explicitly requests or approves that image.

```bash
python3 scripts/export_review_set.py db attach <item_id> --source "<source-image-path>" --role prompt --provenance provided --caption "Network shown in the question"
python3 scripts/export_review_set.py db attachment-audit
```

Attachment metadata changes, detachment, and question deletion are destructive or visibility-sensitive operations and require the user's explicit authorization:

```bash
python3 scripts/export_review_set.py db attachment-update <attachment_id> --role solution --confirmed-by-user
python3 scripts/export_review_set.py db detach <attachment_id> --confirmed-by-user
python3 scripts/export_review_set.py db delete <item_id> --confirmed-by-user
python3 scripts/export_review_set.py db attachment-audit --prune-orphans --empty-trash --confirmed-by-user
```

Without cleanup flags, `db attachment-audit` does not remove attachment files or associations; opening the database may still initialize or migrate its schema. Cleanup flags require `--confirmed-by-user`.

## Command Reference

| Command | Purpose |
| --- | --- |
| `config get` / `config set` | Show or set the notes root. |
| `db validate` | Check import JSON without writing anything. |
| `db add` | Save selected items; an item matching an existing course, title, and original question updates it instead of duplicating it. |
| `db update` | Edit one saved item in place. |
| `db show` | Show one item with its answer, attachments, and review history. |
| `db search` | Search saved text, optionally filtered by course, topic, or review status. |
| `db pending` | List items that are not mastered yet. |
| `db quiz` | Answer-free prompts for a course, topic, or keyword; due items first. |
| `db due` | Answer-free prompts scheduled for today or earlier. |
| `db grade` | Record `correct`, `partial`, or `wrong` and schedule the next review. |
| `db mark-done` | Master an item immediately. |
| `db mark-wrong` | Same as `db grade --result wrong`. |
| `db reset` | Restart the review schedule for a course, topic, or listed items. |
| `db export` | Write a scoped `full` or `questions-only` Markdown sheet. |
| `db stats` | Per-course counts; `--human` prints a readable list and `--topics` adds per-topic counts. |
| `db rename-course` | Rename a course while keeping item ids and review history. |
| `db delete` | Permanently delete one item and its managed images. |
| `db attach` | Attach a verified image to a saved item. |
| `db attachment-update` | Change an image's role, provenance, or caption. |
| `db detach` | Remove one managed image. |
| `db attachment-audit` | Report missing, modified, orphaned, trash, and unsafe image files. |

`db update`, `db reset`, `db rename-course`, `db delete`, `db attachment-update`, `db detach`, and audit cleanup require `--confirmed-by-user`; `db add` requires `--confirmed-selection-by-user`. Without the flag they change nothing, exit with code 5, and most print a `preview` of exactly what would change. Run any command with `--help` for all of its options.

## Exit Codes

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | Error, with the reason on stderr. `PILLOW_REQUIRED` means an attachment command needs Pillow. |
| 2 | Invalid command arguments. |
| 3 | `NOT_CONFIGURED`: no notes root has been set yet. |
| 4 | `NOTES_ROOT_MISSING` or `NOTES_DB_MISSING`: the configured folder or its database is unavailable. |
| 5 | Confirmation required; nothing changed. |

## JSON Input Format

Imports contain a course, collection type, topic, and one or more complete items. The `attachments` list is optional; this compact example includes one question-visible image:

```json
{
  "course": "Transport Planning",
  "collection_type": "question_set",
  "topic": "Network Equilibrium",
  "items": [
    {
      "title": "Choose a toll link",
      "original_question": "Which link should be tolled in this two-link network?",
      "knowledge_points": ["Second-best pricing"],
      "mistake_reason": "Needs review",
      "correct_approach": "Compare equilibria under a toll on each candidate link.",
      "answer_points": ["Use the tolled equilibrium that minimizes total travel cost."],
      "review_suggestion": "Re-solve one parallel-link toll example.",
      "attachments": [
        {
          "source_path": "<source-image-path>",
          "role": "prompt",
          "provenance": "provided",
          "caption": "Network shown in the question"
        }
      ]
    }
  ]
}
```

See [references/schema.md](references/schema.md) for required item fields, accepted `collection_type` values, and attachment validation rules. When updating an existing item, omitting `attachments` or providing an empty list preserves its current attachments.

## Managed Image Attachments

SQLite stores attachment metadata, including role, provenance, managed relative path, media information, and SHA-256 digest. Image bytes live as files inside the managed notes-root attachments directory rather than as database BLOBs.

| Value | Meaning and visibility |
| --- | --- |
| `prompt` | Question-visible material. It can appear in `db quiz`, `db due`, and `db export --mode questions-only`. |
| `solution` | Answer-revealing material. It must not appear in `db quiz`, `db due`, or `questions-only` exports. |
| `provided` | The saved source is an accessible image file supplied for preservation. |
| `reconstructed` | The image is a faithful local recreation, not an original source image. |

`db show` returns both roles so the agent can show solution material after the learner has answered.

`db validate` verifies a referenced source without copying it. `db add` and `db attach` re-check an immutable working snapshot before installing it: input is bounded to 25 MiB, decoded as PNG, JPEG, or WebP, and recorded with a SHA-256 digest. Managed-file access rejects unsafe symbolic-link or Windows-junction paths; `db attachment-audit` reports missing, modified, orphaned, leftover-trash, and unsafe-path conditions without following unsafe links for cleanup.

## Safety And Privacy

- Keep the notes root, database, exports, and source or managed images local and private unless you intentionally share an export. They are user data, not repository content.
- Never commit a configured notes root, database path, source-image path, or managed attachment path to this repository; use placeholders in issues, pull requests, and documentation.
- Saving requires explicit candidate selection before `db add --confirmed-selection-by-user`.
- Changing attachment visibility through `db attachment-update`, removing an attachment through `db detach`, removing a question through `db delete`, editing a question through `db update`, and restarting review progress through `db reset` require `--confirmed-by-user` after explicit authorization.
- If the notes root is in a cloud-synced folder, let it finish syncing before using the skill on another device. For a manual backup, copy `exercise_bank.sqlite3` together with `attachments/` while no command is running.
- If `db attachment-audit` reports `unsafe_paths`, the skill must not open, render, move, or delete the linked files; confirmed detach or deletion may remove only the database association and leave external files for the user to handle.

## Testing

From the skill directory, use a Python 3 interpreter with the required dependencies:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/export_review_set.py tests/test_export_review_set.py
git diff --check
```

## Contributing And Releases

Keep changes scoped, preserve the SQLite-first and confirmation-based safety model, and update both README languages whenever public behavior or commands change. A typical contribution uses a `feature/<description>` branch and a pull request targeting `main`, with validation commands recorded in the pull request.

For releases or user-facing documentation changes, describe migration or safety effects clearly and do not publish local databases, exports, exercise content, or managed images.

## Documentation

- [Operational skill instructions](SKILL.md)
- [JSON input schema](references/schema.md)
- [Managed image attachment design](docs/superpowers/specs/2026-05-24-question-image-attachments-design.md)
