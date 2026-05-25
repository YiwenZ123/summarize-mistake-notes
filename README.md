# summarize-mistake-notes

[简体中文](README.zh-CN.md)

`summarize-mistake-notes` is a Codex skill for turning study questions, corrections, and mistake notes into a structured exercise bank. It keeps searchable content and review progress in SQLite, while supporting guided self-testing, scoped Markdown exports, and optional image attachments for questions that depend on visual information.

## Capabilities

| Capability | What it provides |
| --- | --- |
| SQLite source of truth | Questions, classifications, answers, and review state live in one exercise database rather than scattered Markdown state files. |
| Guided review state | Quiz and due-review flows expose prompts without answer content, then record correct or wrong attempts. |
| Scoped Markdown exports | Create shareable full review sheets or `questions-only` self-test sheets for a selected course or topic. |
| Managed image attachments | Preserve essential `prompt` or `solution` images as managed files while SQLite stores their metadata. |
| Safety and integrity | Explicit save/deletion confirmations, image verification, SHA-256 integrity metadata, and attachment auditing protect local study material. |

## How It Works

1. Codex gathers candidate review items and obtains the learner's explicit selection before saving.
2. Structured JSON is validated and then written through `scripts\export_review_set.py` into `<notes_root>\exercise_bank.sqlite3`.
3. Search, quiz, due-review, and review-event commands read and update the SQLite exercise bank.
4. Markdown exports are generated only for sharing, backup, or self-testing; they are not the review-state store.
5. Essential images may be copied under `<notes_root>\attachments\`, with role-aware display rules that keep solution material out of self-tests.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `SKILL.md` | Operational instructions followed by Codex during save, search, review, export, and attachment tasks. |
| `scripts\export_review_set.py` | SQLite, export, attachment-management, and CLI implementation. |
| `references\schema.md` | JSON import contract, including optional attachment fields. |
| `tests\test_export_review_set.py` | Behavior and safety tests for the CLI workflow. |
| `docs\superpowers\specs\2026-05-24-question-image-attachments-design.md` | Design rationale and safety contract for managed images. |

## Requirements

- A Codex environment that can load a local skill directory.
- A Python 3 environment with Pillow installed is required to run the CLI because the script imports Pillow at startup. Image attachments remain optional functionality; SQLite is provided by Python's standard library.
- Git for cloning, contribution, and repository validation workflows.

## Installation

Clone this repository and place the resulting folder in a skill directory recognized by your Codex installation. This repository describes a local skill layout; it does not claim an official installer.

```powershell
git clone https://github.com/YiwenZ123/summarize-mistake-notes.git C:\Path\To\CodexSkills\summarize-mistake-notes

$Python = "C:\Path\To\Python\python.exe"
$Script = "C:\Path\To\CodexSkills\summarize-mistake-notes\scripts\export_review_set.py"
& $Python $Script --help
```

`SKILL.md` contains fixed local script and runtime paths for an installed operating environment. Those paths are environment-specific operational instructions, not a portable installation command; configure `$Python` and `$Script` for your own installation when invoking the CLI directly.

## Configure Storage

Choose a private local notes root outside the repository. By default, the database is stored at `<notes_root>\exercise_bank.sqlite3`, managed images at `<notes_root>\attachments\`, and generated Markdown exports at `<notes_root>\exports\`.

```powershell
$Python = "C:\Path\To\Python\python.exe"
$Script = "C:\Path\To\CodexSkills\summarize-mistake-notes\scripts\export_review_set.py"

& $Python $Script config set --notes-root "D:\Study\ReviewNotes"
& $Python $Script config get
```

Never hand-edit the SQLite database. Create, search, review, export, update, and delete content through the script.

## Typical Workflows

The examples below assume `$Python` and `$Script` have been defined as shown above.

### Validate And Save Selected Items

Prepare JSON in the format described below. The `--confirmed-selection-by-user` flag must be used only after the learner explicitly selected which candidate items to save. Include an essential image in an import only after the learner explicitly requests or approves it.

```powershell
& $Python $Script db validate --input "D:\Study\Imports\review-set.json"
& $Python $Script db add --input "D:\Study\Imports\review-set.json" --confirmed-selection-by-user
```

### Inspect And Search

```powershell
& $Python $Script db stats --human
& $Python $Script db search --query "shortest path" --limit 10 --include-content
```

### Review And Record Results

Use `db quiz` for a selected course or `db due` for due pending prompts. Correct answers can be completed; wrong or incomplete answers remain pending with a note.

```powershell
& $Python $Script db quiz --course "Transport Planning" --limit 3
& $Python $Script db due --limit 3
& $Python $Script db mark-done <item_id>
& $Python $Script db mark-wrong <item_id> --note "Missed the capacity constraint."
```

### Export A Scoped Review Sheet

An export must be scoped by `--course`, `--topic`, or both. Use `full` when answers are appropriate and `questions-only` for self-testing.

```powershell
& $Python $Script db export --course "Transport Planning" --mode full
& $Python $Script db export --topic "Network Equilibrium" --mode questions-only
```

### Attach And Audit Images

Use an attachment only when an image is essential to understanding or solving the saved question, or when the learner explicitly requests preservation. Attach it only after the learner explicitly requests or approves that image.

```powershell
& $Python $Script db attach <item_id> --source "D:\Study\Sources\network.png" --role prompt --provenance provided --caption "Network shown in the question"
& $Python $Script db attachment-audit
```

Attachment metadata changes, detachment, and question deletion are destructive or visibility-sensitive operations and require the user's explicit authorization:

```powershell
& $Python $Script db attachment-update <attachment_id> --role solution --confirmed-by-user
& $Python $Script db detach <attachment_id> --confirmed-by-user
& $Python $Script db delete <item_id> --confirmed-by-user
& $Python $Script db attachment-audit --prune-orphans --empty-trash --confirmed-by-user
```

Without cleanup flags, `db attachment-audit` does not remove attachment files or associations; opening the database may still initialize or migrate its schema. Cleanup flags require `--confirmed-by-user`.

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
          "source_path": "D:\\Study\\Sources\\network.png",
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

`db validate` verifies a referenced source without copying it. `db add` and `db attach` re-check an immutable working snapshot before installing it: input is bounded to 25 MiB, decoded as PNG, JPEG, or WebP, and recorded with a SHA-256 digest. Managed-file access rejects unsafe symbolic-link or Windows-junction paths; `db attachment-audit` reports missing, modified, orphaned, leftover-trash, and unsafe-path conditions without following unsafe links for cleanup.

## Safety And Privacy

- Keep the notes root, database, exports, and source or managed images local and private unless you intentionally share an export. They are user data, not repository content.
- Use only generic example files such as `D:\Study\ReviewNotes` and `D:\Study\Sources\network.png` in issues, pull requests, and documentation.
- Saving requires explicit candidate selection before `db add --confirmed-selection-by-user`.
- Changing attachment visibility through `db attachment-update`, removing an attachment through `db detach`, and removing a question through `db delete` require `--confirmed-by-user` after explicit authorization.
- If `db attachment-audit` reports `unsafe_paths`, the skill must not open, render, move, or delete the linked files; confirmed detach or deletion may remove only the database association and leave external files for the user to handle.

## Testing

From the repository root, use a Python interpreter with the required dependencies:

```powershell
$Python = "C:\Path\To\Python\python.exe"

& $Python -m unittest discover -s tests -v
& $Python -m py_compile scripts\export_review_set.py tests\test_export_review_set.py
git diff --check
```

## Contributing And Releases

Keep changes scoped, preserve the SQLite-first and confirmation-based safety model, and update both README languages whenever public behavior or commands change. A typical contribution uses a `codex/<description>` feature branch and a pull request targeting `main`, with validation commands recorded in the pull request.

For releases or user-facing documentation changes, describe migration or safety effects clearly and do not publish local databases, exports, exercise content, or managed images.

## Documentation

- [Operational skill instructions](SKILL.md)
- [JSON input schema](references/schema.md)
- [Managed image attachment design](docs/superpowers/specs/2026-05-24-question-image-attachments-design.md)
