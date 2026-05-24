---
name: summarize-mistake-notes
description: Use when the user asks to save, add, search, export, review, quiz, revisit, filter, rename course classifications, or mark completed study questions, mistake notes, wrong answers, uncertain answers, homework corrections, exam review notes, course practice items, exercise-bank items, or asks to quiz/test them from saved study material.
---

# Mistake Note Organizer

## Overview

Use a SQLite exercise bank to store study questions, mistake notes, and review state. Markdown export is only a backup/share format; never use Markdown as the primary store or review state.

## Guardrails

Use the fixed command paths below. Do not search the current chat directory for this skill, do not try multiple Python launchers before the fixed runtime, and do not narrate skill loading, config checks, script discovery, validation, or Python troubleshooting to the user unless execution is truly blocked.

## User-Facing Rules

- The first user-facing sentence must advance the study task itself. Do not say that you are reading the skill, checking scripts, or inspecting config.
- Run technical checks silently. Mention them only when the save location is missing or a fixed command fails and blocks progress.
- For add requests, ask for course/topic/collection classification and candidate-item selection in the same confirmation prompt. Show only candidate titles and original questions, not complete answers.
- Before asking for add confirmation, silently run `<python> <script> db stats --human` and include the returned course list and counts in the same confirmation message. Do not list courses from memory.
- Do not write any item until the user explicitly selects which numbered candidates to save. Accept explicit responses such as `save 1, 3` or `save all`; classification confirmation alone is not authorization to write.
- For export requests, identify a course or topic scope and ask whether to export `full` or `questions-only` if the user has not specified the mode.
- Summarize the save only after the selected items are written successfully.

## Add Questions Fast Path

When the user asks to save the current conversation, make a mistake set, make a question set, or organize recent Q&A, the first user-facing reply must combine classification confirmation with candidate-item selection:

```text
I infer the course is "<course>", the topic is "<topic>", and this should be saved as <question_set|mistake_set>.

Candidate items:
1. <title> - <original question>
2. <title> - <original question>

Please confirm the classification and which items to save, for example "classification correct, save 1" or "classification correct, save all".

Available courses:
1. <course> (<pending> pending / <total> total)
2. <course> (<pending> pending / <total> total)
```

Before that reply, only run `<python> <script> db stats --human` silently. Do not read the current directory, run `rg`, search for scripts, explain config, try system `python`/`py`, or show complete answer content.

If the course cannot be inferred, ask:

```text
Which course should this content belong to? I will save it as <question_set|mistake_set>.

Candidate items:
1. <title> - <original question>
2. <title> - <original question>

Please provide the course and which items to save.

Available courses:
1. <course> (<pending> pending / <total> total)
2. <course> (<pending> pending / <total> total)
```

After the user confirms the classification and identifies the items to save:

1. Silently check the save location; if missing, ask only for a folder path.
2. Prepare JSON with original question, answer points, correct approach, and review suggestion for the selected items only. Read `references/schema.md` only when field details are needed.
3. Run `db validate`, then `db add --confirmed-selection-by-user`.
4. Reply `Saved <N> items to <course> / <topic>`. Do not show internal JSON unless the user asks.

If the user confirms the classification but does not choose items, ask which candidate numbers to save and do not run `db add`.

## Review Fast Path

When the user asks to review, quiz, random-review, or be tested, first silently read the course list:

```powershell
<python> <script> db stats --human
```

Do not search files, inspect the current directory, run `rg`, explain environment setup, or look for the script path. List only courses returned by `db stats --human`.

If courses exist, the first user-facing reply must list them:

```text
Which course or topic do you want to review? You can also say "review anything".

Available courses:
1. <course> (<pending> pending / <total> total)
2. <course> (<pending> pending / <total> total)
```

If no course exists, reply:

```text
The exercise bank does not have any course records yet. You can first ask me to turn a conversation into a question set or mistake set.
```

If the user gives a course or keyword:

```powershell
<python> <script> db quiz --course "<user answer>" --limit 3
```

If the user says "review anything", "random", or "anything is fine":

```powershell
<python> <script> db due --limit 3
```

Ask one item at a time. Do not use `db pending --include-content` for quizzing because it includes answer content. Mark correct answers with `db mark-done <item_id>`. For wrong or incomplete answers, run `db mark-wrong <item_id> --note "<brief reason>"` and keep the item pending.

When the selected item returned by `db quiz` or `db due` includes attachments, display every available `prompt` attachment together with the question text in the same quiz message, in the returned order. Render each local image from its absolute `managed_path` using Markdown image syntax, such as `![<caption>](<managed_path>)`. If a `prompt` attachment has `missing: true` or cannot be displayed, still ask the text question and state briefly that the question image is currently unavailable. Never display a `solution` attachment while asking a quiz question.

## Command Paths

Use these absolute paths:

```text
<script> = C:\Users\Zippe\.codex\skills\summarize-mistake-notes\scripts\export_review_set.py
```

On Windows, always use the fixed Python runtime. Do not try system `python` or `py` first:

```text
<python> = C:\Users\Zippe\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

During normal review, do not describe environment troubleshooting to the user. Only mention a blocker if the fixed command truly fails and there is no way to continue.

## Storage Setup

Check the configured save location only when the task actually needs database access; never make this the first user-facing reply.

1. Silently check the fixed save location:
   ```powershell
   <python> <script> config get
   ```
2. If not configured, ask only:
   ```text
   The exercise bank does not have a fixed save location yet. Which folder should store the exercise database? Please provide a folder path.
   ```
3. Save the location:
   ```powershell
   <python> <script> config set --notes-root "<folder path>"
   ```
4. The default database path is:
   ```text
   <notes_root>\exercise_bank.sqlite3
   ```

Never hand-edit the database file. All create/read/update operations must go through the script.

## Add Questions

After the user confirms course/topic/collection type and explicitly selects candidate items, prepare JSON containing only selected items and validate it:

```powershell
<python> <script> db validate --input "<prepared-json-file>"
```

Then add it:

```powershell
<python> <script> db add --input "<prepared-json-file>" --confirmed-selection-by-user
```

Use `collection_type` values `mistake_set` or `question_set`. Never add candidates the user excluded or did not select. Re-adding the same course, title, and original question updates the existing item instead of creating a duplicate.

## Optional Image Attachments

### Attachment Decision Rule

Attach an image only when its visual information is necessary to solve or
understand a question, such as a network diagram, geometry figure, chart,
table, labelled map, hard-to-transcribe symbols, or when the user explicitly
requests preservation. Do not attach decorative images or an image that adds
nothing beyond complete and unambiguous text.

Classify question-visible material as `prompt` and answer-revealing material
as `solution`. Use provenance `provided` for an accessible source image and
`reconstructed` for a faithful local recreation; never describe a reconstructed
image as the original.

Solution images must not appear in `db quiz`, `db due`, or `questions-only` exports.
Use `full` export or `db search --include-content` only when showing answer
material is appropriate.

For quiz output, returning a `prompt` attachment is an instruction to display
it with the question, not merely metadata to ignore. Display all available
question images before waiting for the user's answer.

After the user has selected a candidate question and explicitly requested or
approved an essential image, include the optional attachment object documented
in `references/schema.md`, then use the normal validation and add flow:

```powershell
<python> <script> db validate --input "<prepared-json-file>"
<python> <script> db add --input "<prepared-json-file>" --confirmed-selection-by-user
```

For an already-saved question, add an image without replacing the question:

```powershell
<python> <script> db attach <item_id> --source "<image-path>" --role prompt --provenance provided --caption "<caption>"
```

Changing an attachment role can reveal answer material during review. Only run
metadata changes or deletion after explicit user authorization:

```powershell
<python> <script> db attachment-update <attachment_id> --role <prompt|solution> --confirmed-by-user
<python> <script> db detach <attachment_id> --confirmed-by-user
<python> <script> db delete <item_id> --confirmed-by-user
```

Use `db attachment-audit` to report missing, modified, orphaned, or uncleaned
managed files. Only use its cleanup flags after explicit user authorization.
If an audit reports `unsafe_paths`, do not open, render, move, or delete those
linked files through the skill. After explicit authorization, `db detach` or
`db delete` may remove the database association and return
`file_cleanup_skipped`; the external linked file remains for the user to
handle directly.

## Search Questions

Search by keyword:

```powershell
<python> <script> db search --query "derivative" --limit 10 --include-content
```

Search by course:

```powershell
<python> <script> db search --course "Calculus" --limit 10 --include-content
```

Search only pending items:

```powershell
<python> <script> db search --review-status pending --limit 10
```

View JSON course stats:

```powershell
<python> <script> db stats
```

View a user-facing course list:

```powershell
<python> <script> db stats --human
```

## Export Questions

Use database export when the user wants a Markdown file containing saved questions from a
specified course or topic. Ask whether to export `full` or `questions-only` if no mode is given.
If no course or topic is given, ask for the export scope first. Do not export all questions when neither a course nor a topic is specified.

Export a full question-and-answer file:

```powershell
<python> <script> db export --course "<course>" --mode full
```

Export a self-test file containing titles and question prompts only:

```powershell
<python> <script> db export --topic "<topic>" --mode questions-only
```

To narrow a topic to one course, provide both filters. Saved files are written under
`<notes_root>\exports\`. A `full` export contains only each question title, `### 问题`, and `### 回答`.
The answer is rendered from saved answer points. Do not include database metadata, review status, knowledge points, correct approach, or review suggestion.
A `questions-only` export contains only each title and `### 问题`; it must not reveal answer points.

## Rename Courses

Only run a course rename after the user explicitly confirms the exact old and new course names.
Do not infer authorization from translation, spelling cleanup, duplicate-looking courses, or a
suggested reorganization. Never silently merge an existing target course into another course.

Before asking for confirmation, run `db stats --human` to identify the current course name and
tell the user the proposed new name. After confirmation, rename through the database command:

```powershell
<python> <script> db rename-course "<old-course>" "<new-course>" --confirmed-by-user
```

The command preserves question IDs and review history while updating the course classification and
search index. After it succeeds, run `db stats --human` to verify the renamed course and counts.

## Delete Questions

Only run deletion after the user explicitly requests permanent deletion or confirms it. Do not
infer authorization from a duplicated, incomplete, incorrect, skipped, or inconvenient question.

Before deletion, identify the exact stored question and item ID. After confirmation, delete only
that ID:

```powershell
<python> <script> db delete <item_id> --confirmed-by-user
```

After deletion, search for the removed item or run `db stats --human` to verify the result.

## Markdown Compatibility

Markdown export is only for backup, sharing, or self-test sheets. It does not create, read, or
update review state:

```powershell
<python> <script> export --input "<prepared-json-file>"
```

New notes, searches, review prompts, and completion state must use SQLite `db` commands. Do not use old `review list`, `review mark-done`, or `REVIEW_CHECKLIST.md` workflows.

## Quality Bar

- Save only clear study questions, mistake notes, corrections, or review-worthy content. Do not save casual chat or tool-operation issues.
- Mistake reasons must be specific, such as "confused function value with rate of change"; do not write only "careless".
- Review suggestions must be actionable, such as "redo three similar tangent-slope problems".
- Wrong, skipped, or answer-peeking attempts must not be marked done.
- For "review anything", use `db due` so older pending items come first.

## Common Mistakes

- Using `REVIEW_CHECKLIST.md` or `review` commands for review state. Review state lives only in SQLite.
- Adding items before showing candidate questions and obtaining explicit item selection.
- Treating classification confirmation as permission to save every candidate item.
- Showing complete answers before the user selects which items to save.
- Narrating internal steps such as reading the skill, checking config, finding scripts, or switching Python.
- Continuing after PowerShell shows mojibake. Re-read with `Get-Content -Encoding UTF8`.
- Trying system `python` or `py` before the fixed Python runtime.
- Searching Markdown files instead of using `db search`.
- Quizzing with `db pending --include-content`; use `db quiz` or `db due` instead.
- Omitting an available `prompt` attachment when displaying a quiz question returned by `db quiz` or `db due`.
- Exporting all stored questions without the user choosing a course or topic scope.
- Including answers in a `questions-only` export, or including internal review metadata in either exported reading format.
- Marking wrong answers with `mark-done`; wrong answers must use `mark-wrong`.
- Revealing a `solution` image through `db quiz`, `db due`, or a `questions-only` export.
- Changing, detaching, or deleting image attachments without explicit user authorization.
- Renaming a course without the user's explicit confirmation of both the old and new names.
- Deleting a question without the user's explicit request or confirmation of permanent deletion.
- Asking what to review without listing courses from `db stats --human`.
