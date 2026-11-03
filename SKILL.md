---
name: summarize-mistake-notes
description: Organizes study questions, corrections, and mistake notes into a searchable SQLite exercise bank with spaced-repetition review, scoped Markdown exports, and optional image attachments. Use when the user asks to save, add, search, edit, export, review, quiz, revisit, filter, rename course classifications, reset review progress, or mark completed study questions, mistake notes, wrong answers, uncertain answers, homework corrections, exam review notes, course practice items, exercise-bank items, or asks to quiz/test them from saved study material or see what is due for review.
---

# Mistake Note Organizer

## Overview

Use a SQLite exercise bank to store study questions, mistake notes, and review schedules. Markdown export is only a backup/share format; never use Markdown as the primary store or review state.

## Guardrails

Resolve the script relative to this `SKILL.md` and use any Python 3.9+ runtime. Pillow is needed only for image attachments; attachment commands report `PILLOW_REQUIRED` when it is missing. Do not resolve the script from the user's current workspace, store machine-specific absolute paths or a configured notes root in this skill, or narrate skill loading, config checks, script discovery, validation, or Python troubleshooting to the user unless execution is truly blocked.

## User-Facing Rules

- The first user-facing sentence must advance the study task itself. Do not say that you are reading the skill, checking scripts, or inspecting config.
- Write every user-facing message in the learner's language; the templates below show the structure, not the required wording.
- Run technical checks silently. Mention them only when the save location is missing or a resolved command fails and blocks progress.
- For add requests, ask for course/topic/collection classification and candidate-item selection in the same confirmation prompt. Show only candidate titles and original questions, not complete answers.
- Before asking for add confirmation, silently run `<python> <script> db stats --human --topics` and include the returned course list and counts in the same confirmation message. Reuse an existing course or topic name when one fits. Do not list courses from memory.
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
1. <course> (<due> due / <pending> pending / <total> total)
   - <topic> (<due> due / <pending> pending / <total> total)
```

Before that reply, only run `<python> <script> db stats --human --topics` silently. Do not read the current directory, run `rg`, search for scripts, explain config, try system `python`/`py`, or show complete answer content.

If the course cannot be inferred, ask:

```text
Which course should this content belong to? I will save it as <question_set|mistake_set>.

Candidate items:
1. <title> - <original question>
2. <title> - <original question>

Please provide the course and which items to save.

Available courses:
1. <course> (<due> due / <pending> pending / <total> total)
   - <topic> (<due> due / <pending> pending / <total> total)
```

After the user confirms the classification and identifies the items to save:

1. Silently check the save location; if missing, follow Storage Setup.
2. Prepare JSON with original question, answer points, correct approach, and review suggestion for the selected items only. Read `references/schema.md` only when field details are needed.
3. Run `db validate`, then `db add --confirmed-selection-by-user`.
4. Reply `Saved <N> items to <course> / <topic>`. If the result counts `updated` items, say they already existed and were updated; if it lists `requeued` items, say they were returned to review. Do not show internal JSON unless the user asks.

If the user confirms the classification but does not choose items, ask which candidate numbers to save and do not run `db add`.

## Review Fast Path

When the user asks to review, quiz, random-review, or be tested, first silently read the course and topic list:

```bash
<python> <script> db stats --human --topics
```

Do not search files, inspect the current directory, run `rg`, explain environment setup, or look for the script path. List only courses and topics returned by `db stats --human --topics`.

If the request already names a scope (a course, a topic, a keyword, "anything", or "random", in any language), skip the question below and run the matching command from the table. Otherwise, if courses exist, the first user-facing reply must list them:

```text
Which course or topic do you want to review? You can also say "review anything".

Available courses:
1. <course> (<due> due / <pending> pending / <total> total)
   - <topic> (<due> due / <pending> pending / <total> total)
```

In the counts, `due` means scheduled for today or earlier and `pending` means not mastered yet.

If no course exists, reply:

```text
The exercise bank does not have any course records yet. You can first ask me to turn a conversation into a question set or mistake set.
```

Choose the command from what the user names, using the exact course or topic name from the list:

| The user names | Command |
| --- | --- |
| A course | `<python> <script> db quiz --course "<course>" --limit 3` |
| A topic | `<python> <script> db quiz --topic "<topic>" --limit 3` |
| A keyword that is not a course or topic | `<python> <script> db quiz --query "<keyword>" --limit 3` |
| "Review anything" or "what is due" | `<python> <script> db due --limit 3` |
| "Random" | `<python> <script> db due --shuffle --limit 3` |
| A whole course before an exam, including mastered items | `<python> <script> db quiz --course "<course>" --include-done --limit 3` |

`db due` returns only items scheduled for today or earlier, most overdue first. If it returns nothing, say nothing is due today. When the learner has asked for more questions, continue directly with practice from `<python> <script> db quiz --shuffle --limit 3`, optionally limited to a course or topic, and say these are practice items that will not move the schedule; otherwise offer that practice.

### Quiz Loop

Ask one item at a time:

1. Show the question text and every available `prompt` image, then wait for the user's answer.
2. After the user answers, silently run `<python> <script> db show <item_id>` to read the saved answer points, correct approach, mistake reason, and review history. Never run `db show` before the user has answered; it reveals the answer.
3. Judge the answer against the saved answer points. `correct` means every answer point is right. `partial` means the approach is right and at least one answer point is fully right, but another is missing or wrong. `wrong` means the key result is wrong or missing. Skipped, "I don't know", and answer-peeking attempts are `wrong`.
4. Record the result with `<python> <script> db grade <item_id> --result <correct|partial|wrong> --note "<brief reason>"`. A note is required for `partial` and `wrong`, for example "gave the formula but not the stability condition".
5. Give feedback from the saved correct approach; `solution` images from `db show` may be shown now. If the answer repeats the saved mistake reason or a mistake recorded in the history, point that out. Tell the user the next review date from `schedule.next_review_at`, or that the item is mastered when `schedule.mastered` is true.
6. Continue with the next returned item. When the returned items run out, run the same command again if it returned as many items as `--limit`; if it returned fewer items than `--limit`, say the set is finished and offer to stop or practice more. If the learner changes how they want to review, pick the matching command from the table.

The CLI applies the schedule: a correct answer on a due item moves it to the next interval (1, 3, 7, 14, then 30 days) and masters it after the 30-day review; a correct answer on an item that is not due yet counts as practice and leaves the schedule unchanged (`schedule.advanced` is false); `partial` keeps the stage and brings the item back tomorrow; `wrong` restarts the schedule from tomorrow.

`mark-done` masters an item permanently and removes it from future reviews; use it only when the user explicitly says an item no longer needs review. That explicit request is the authorization, so it takes no confirmation flag, and `db reset` can bring the item back. If the learner asks to stop reviewing an item instead of answering it, run `<python> <script> db mark-done <item_id>` without grading the attempt and without showing the saved answer, which its output contains; this takes precedence over grading a skipped attempt as `wrong`. Then confirm that the item is mastered and continue. `mark-wrong` is the same as `db grade --result wrong`.

Do not use `db pending --include-content` or `db search --include-content` for quizzing because they include answer content.

When the selected item returned by `db quiz` or `db due` includes attachments, display every available `prompt` attachment together with the question text in the same quiz message, in the returned order. Render each local image from its absolute `managed_path` using Markdown image syntax, such as `![<caption>](<managed_path>)`. If a `prompt` attachment has `missing: true` or cannot be displayed, still ask the text question and state briefly that the question image is currently unavailable. Never display a `solution` attachment while asking a quiz question.

### Reset Review Progress

When the user wants to restart a course or topic, for example to revise everything before an exam, run the reset without confirmation first. It changes nothing and returns a preview; tell the user how many items will restart and ask for confirmation before applying it:

```bash
<python> <script> db reset --course "<course>"
<python> <script> db reset --course "<course>" --confirmed-by-user
```

Use `--topic "<topic>"` or specific item ids for a narrower reset.

## Command Paths

Resolve these placeholders for the active agent environment at runtime:

```text
<script> = <skill-directory>/scripts/export_review_set.py
<python> = <python-3.9-or-newer-runtime>
```

Resolve `<skill-directory>` from this `SKILL.md`, not from the user's current workspace. Image attachment commands additionally need Pillow in that runtime. Command options, including `--config`, `--db-path`, and `--notes-root`, go after the full subcommand, for example `<python> <script> db stats --human --config "<config-file>"`. Never replace the placeholders in the tracked skill with a developer-specific absolute path or notes-root value. During normal review, do not describe environment troubleshooting to the user. Only mention a blocker if the resolved command truly fails and there is no way to continue.

## Storage Setup

No notes root is preconfigured by this skill or repository. Check the configured save location only when the task actually needs database access; never make this the first user-facing reply. The default CLI config file is `~/.config/summarize-mistake-notes/config.json`; use `--config` when a different config file is needed.

The fast paths run `db stats` directly instead of `config get`; whenever any command exits with code 3 or 4, handle it as described in this section.

1. Silently check the configured save location:
   ```bash
   <python> <script> config get
   ```
2. If it reports `NOT_CONFIGURED`, ask only:
   ```text
   The exercise bank does not have a configured save location yet. Which folder should store the exercise database? Please provide a folder path.
   ```
3. Save the location:
   ```bash
   <python> <script> config set --notes-root "<folder path>"
   ```
4. The default database path is:
   ```text
   <notes_root>/exercise_bank.sqlite3
   ```

If any command reports `NOTES_ROOT_MISSING` or `NOTES_DB_MISSING`, the saved folder or its database is unavailable, for example because it was moved, renamed, disconnected, or a cloud folder has not finished syncing. Tell the user which path is missing and ask whether to restore it or choose another folder. Never create a replacement folder or database, and run `config set` only with a folder the user chooses.

Never hand-edit the database file. All create/read/update operations must go through the script.

## Exit Codes

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | Success | Continue. |
| 1 | Error; the reason is on stderr | Report it once; do not retry in a loop. |
| 2 | Invalid command arguments | Fix the command. |
| 3 | `NOT_CONFIGURED` | Ask for a folder as described in Storage Setup. |
| 4 | `NOTES_ROOT_MISSING` or `NOTES_DB_MISSING` | Ask the user about the missing location; never recreate it. |
| 5 | Confirmation required; nothing changed | Show the `preview` printed on stdout, ask the user, then rerun with the confirmation flag. |

## Add Questions

After the user confirms course/topic/collection type and explicitly selects candidate items, prepare JSON containing only selected items and validate it:

```bash
<python> <script> db validate --input "<prepared-json-file>"
```

Then add it:

```bash
<python> <script> db add --input "<prepared-json-file>" --confirmed-selection-by-user
```

Use `collection_type` values `mistake_set` or `question_set`. Never add candidates the user excluded or did not select. Re-adding the same course, title, and original question updates the existing item instead of creating a duplicate, including after a course rename. Re-saving an existing `mistake_set` item restarts its review schedule and lists it under `requeued`.

## Edit Questions

To fix a typo, correct an answer, or move an item to another course or topic, write a JSON object containing only the fields to change (see `references/schema.md`). Run the update without confirmation to get a preview, show the changes to the user, and apply them only after the user confirms:

```bash
<python> <script> db update <item_id> --input "<changes-json-file>"
<python> <script> db update <item_id> --input "<changes-json-file>" --confirmed-by-user
```

The item keeps its id, attachments, and review history. Do not re-add an edited question as a new item.

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
Use `full` export, `db show` after the user has answered, or `db search --include-content`
only when showing answer material is appropriate.

For quiz output, returning a `prompt` attachment is an instruction to display
it with the question, not merely metadata to ignore. Display all available
question images before waiting for the user's answer.

After the user has selected a candidate question and explicitly requested or
approved an essential image, include the optional attachment object documented
in `references/schema.md`, then use the normal validation and add flow:

```bash
<python> <script> db validate --input "<prepared-json-file>"
<python> <script> db add --input "<prepared-json-file>" --confirmed-selection-by-user
```

For an already-saved question, add an image without replacing the question:

```bash
<python> <script> db attach <item_id> --source "<image-path>" --role prompt --provenance provided --caption "<caption>"
```

Changing an attachment role can reveal answer material during review. Only run
metadata changes or deletion after explicit user authorization:

```bash
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

`--query` searches all saved text; `--course` and `--topic` match name substrings and can be combined:

```bash
<python> <script> db search --query "derivative" --limit 10 --include-content
<python> <script> db search --course "Calculus" --topic "Derivatives" --limit 10 --include-content
<python> <script> db search --review-status pending --limit 10
```

View course stats as JSON, or as a user-facing list with per-topic counts:

```bash
<python> <script> db stats
<python> <script> db stats --human --topics
```

## Export Questions

Use database export when the user wants a Markdown file containing saved questions from a
specified course or topic. Ask whether to export `full` or `questions-only` if no mode is given.
If no course or topic is given, ask for the export scope first. Do not export all questions when neither a course nor a topic is specified.

Export a full question-and-answer file:

```bash
<python> <script> db export --course "<course>" --mode full
```

Export a self-test file containing titles and question prompts only:

```bash
<python> <script> db export --topic "<topic>" --mode questions-only
```

To narrow a topic to one course, provide both filters. Saved files are written under
`<notes_root>/exports/`. A `full` export contains only each question title, `### 问题`, and `### 回答`.
The answer is rendered from saved answer points. Do not include database metadata, review status, knowledge points, correct approach, or review suggestion.
A `questions-only` export contains only each title and `### 问题`; it must not reveal answer points.

## Rename Courses

Only run a course rename after the user explicitly confirms the exact old and new course names.
Do not infer authorization from translation, spelling cleanup, duplicate-looking courses, or a
suggested reorganization. Never silently merge an existing target course into another course.

Before asking for confirmation, run `db stats --human` to identify the current course name and
tell the user the proposed new name. Running the rename without `--confirmed-by-user` changes
nothing and returns a preview with the number of affected questions. After confirmation, rename
through the database command:

```bash
<python> <script> db rename-course "<old-course>" "<new-course>" --confirmed-by-user
```

The command preserves question IDs and review history while updating the course classification and
search index. After it succeeds, run `db stats --human` to verify the renamed course and counts.

## Delete Questions

Only run deletion after the user explicitly requests permanent deletion or confirms it. Do not
infer authorization from a duplicated, incomplete, incorrect, skipped, or inconvenient question.

Before deletion, identify the exact stored question and item ID. Running `db delete <item_id>`
without `--confirmed-by-user` changes nothing and prints a preview of the item; show it when asking
for confirmation. After confirmation, delete only that ID:

```bash
<python> <script> db delete <item_id> --confirmed-by-user
```

After deletion, search for the removed item or run `db stats --human` to verify the result.

## Markdown Compatibility

Markdown export is only for backup, sharing, or self-test sheets. It does not create, read, or
update review state:

```bash
<python> <script> export --input "<prepared-json-file>"
```

New notes, searches, review prompts, and completion state must use SQLite `db` commands. Do not use old `review list`, `review mark-done`, or `REVIEW_CHECKLIST.md` workflows.

## Quality Bar

- Save only clear study questions, mistake notes, corrections, or review-worthy content. Do not save casual chat or tool-operation issues.
- Mistake reasons must be specific, such as "confused function value with rate of change"; do not write only "careless".
- Review suggestions must be actionable, such as "redo three similar tangent-slope problems".
- Wrong, skipped, or answer-peeking attempts must be graded `wrong`, never `correct` or `mark-done`, unless the learner explicitly asks to stop reviewing that item.
- For "review anything", use `db due` so the most overdue items come first.

## Common Mistakes

- Using `REVIEW_CHECKLIST.md` or `review` commands for review state. Review state lives only in SQLite.
- Adding items before showing candidate questions and obtaining explicit item selection.
- Treating classification confirmation as permission to save every candidate item.
- Showing complete answers before the user selects which items to save.
- Narrating internal steps such as reading the skill, checking config, finding scripts, or switching Python.
- Continuing after terminal output shows mojibake. Re-read the output as UTF-8 before acting on it.
- Committing an environment-specific runtime path into the skill.
- Searching Markdown files instead of using `db search`.
- Passing a topic or keyword to `--course`; use `--topic` or `--query`.
- Quizzing with `db pending --include-content`; use `db quiz` or `db due` instead.
- Running `db show` before the user answers, or grading from memory instead of the saved answer points.
- Grading a correct quiz answer with `mark-done`; use `db grade --result correct` so the schedule continues.
- Omitting an available `prompt` attachment when displaying a quiz question returned by `db quiz` or `db due`.
- Exporting all stored questions without the user choosing a course or topic scope.
- Including answers in a `questions-only` export, or including internal review metadata in either exported reading format.
- Revealing a `solution` image through `db quiz`, `db due`, or a `questions-only` export.
- Changing, detaching, or deleting image attachments without explicit user authorization.
- Renaming a course, resetting review progress, or editing a saved question without the user's explicit confirmation.
- Deleting a question without the user's explicit request or confirmation of permanent deletion.
- Creating a new folder or database after `NOTES_ROOT_MISSING` or `NOTES_DB_MISSING` instead of asking the user.
- Asking what to review without listing courses from `db stats --human --topics`.
