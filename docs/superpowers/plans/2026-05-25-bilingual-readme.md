# Bilingual GitHub README Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish synchronized English and Simplified Chinese README pages that explain how to install, use, validate, and contribute to the SQLite-first mistake-note skill.

**Architecture:** Add two repository landing-page documents without changing runtime code: `README.md` is the English default GitHub page and `README.zh-CN.md` is its Chinese counterpart. Both documents derive command examples and safety claims from the existing CLI, `SKILL.md`, and `references/schema.md`, and cross-link at the top.

**Tech Stack:** Markdown, Git, GitHub pull requests, Python CLI help and `unittest` for verification.

---

## File Map

- Create `README.md`: English default repository introduction and usage guide.
- Create `README.zh-CN.md`: Simplified Chinese version with matching coverage and commands.
- Keep `docs/superpowers/specs/2026-05-25-bilingual-readme-design.md` as the approved documentation requirements source.
- Keep this file as the implementation and validation checklist.

This is a documentation-only increment. It does not require new runtime tests or
database migrations; the existing suite is rerun to verify that the published
repository remains healthy.

### Task 1: Establish Verified Documentation Inputs

**Files:**
- Read: `SKILL.md`
- Read: `references/schema.md`
- Read: `scripts/export_review_set.py`
- Read: `docs/superpowers/specs/2026-05-25-bilingual-readme-design.md`

- [ ] **Step 1: Capture the top-level and database command surface**

Run:

```powershell
& '<python>' 'scripts\export_review_set.py' --help
& '<python>' 'scripts\export_review_set.py' config --help
& '<python>' 'scripts\export_review_set.py' db --help
```

Expected: exit code `0`; output names `config`, `export`, `db`, plus the
database commands documented in `SKILL.md`, including `attach`,
`attachment-audit`, `quiz`, `due`, and `export`.

- [ ] **Step 2: Capture option spellings for commands shown in examples**

Run:

```powershell
& '<python>' 'scripts\export_review_set.py' config set --help
& '<python>' 'scripts\export_review_set.py' db validate --help
& '<python>' 'scripts\export_review_set.py' db add --help
& '<python>' 'scripts\export_review_set.py' db search --help
& '<python>' 'scripts\export_review_set.py' db quiz --help
& '<python>' 'scripts\export_review_set.py' db due --help
& '<python>' 'scripts\export_review_set.py' db export --help
& '<python>' 'scripts\export_review_set.py' db attach --help
& '<python>' 'scripts\export_review_set.py' db attachment-audit --help
```

Expected: exit code `0`; the README examples use only option names shown by
these help messages.

- [ ] **Step 3: Confirm the approved documentation boundaries**

Review `SKILL.md`, `references/schema.md`, and the approved design and record
these constraints in both READMEs:

- SQLite is the source of truth; Markdown export is for sharing or self-test.
- Files are stored in a managed attachments directory while SQLite stores
  metadata.
- `prompt` images may be shown during review; `solution` images must remain
  hidden in self-test flows.
- Local databases and user images are not repository content.
- Destructive database and attachment operations require explicit user
  confirmation in the skill workflow.

### Task 2: Write The English Default README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Add the landing-page framing and navigation**

Create `README.md` with:

- title `summarize-mistake-notes`;
- language navigation linking to `README.zh-CN.md`;
- a short explanation of the skill as a Codex-oriented SQLite exercise bank;
- a feature list covering structured storage, study review, Markdown exports,
  optional image attachments, integrity safeguards, and tests.

- [ ] **Step 2: Document installation, setup, and repository layout**

Add sections named:

```markdown
## How It Works
## Repository Layout
## Requirements
## Installation
## Configure Storage
```

Explain repository installation generically, state that the fixed local paths
inside `SKILL.md` describe the author's installed environment rather than a
portable install command, and show `config set` / `config get` with generic
Windows paths.

- [ ] **Step 3: Document user and CLI workflows**

Add sections named:

```markdown
## Typical Workflows
## JSON Input Format
## Managed Image Attachments
```

Include verified command blocks for validation/add, search/stats, quiz/due
review, exports, `db attach`, and `db attachment-audit`. Include one compact
generic JSON example with an optional `prompt` attachment and link to
`references/schema.md` for the full input contract.

- [ ] **Step 4: Document safety, testing, and contribution**

Add sections named:

```markdown
## Safety And Privacy
## Testing
## Contributing And Releases
## Documentation
```

State the managed-path, verification, visibility, and local-data boundaries;
show existing validation commands; recommend `codex/<description>` branches
and pull requests to `main`; link to `SKILL.md`, the schema reference, and the
image attachment design.

### Task 3: Write The Chinese README With Matching Coverage

**Files:**
- Create: `README.zh-CN.md`

- [ ] **Step 1: Translate the navigation, overview, and installation sections**

Create `README.zh-CN.md` with a top language link back to `README.md`, using
the same section order and the same literal command names and paths as the
English document.

- [ ] **Step 2: Translate workflows and safety rules precisely**

Include every workflow and boundary documented in the English README. Keep
tokens such as `prompt`, `solution`, `provided`, `reconstructed`,
`questions-only`, `SQLite`, and CLI flags in code formatting so translations
cannot alter their operational meaning.

- [ ] **Step 3: Compare both README outlines**

Run:

```powershell
Select-String -Path README.md,README.zh-CN.md -Pattern '^## '
Select-String -Path README.md,README.zh-CN.md -Pattern 'db attach|db attachment-audit|db quiz|db due|questions-only|solution|prompt'
```

Expected: both files include the same subject areas and the critical image
visibility/safety terms.

### Task 4: Verify And Publish The Documentation

**Files:**
- Add: `README.md`
- Add: `README.zh-CN.md`
- Add: `docs/superpowers/plans/2026-05-25-bilingual-readme.md`

- [ ] **Step 1: Validate links, formatting, and repository state**

Run:

```powershell
Test-Path README.md
Test-Path README.zh-CN.md
Test-Path SKILL.md
Test-Path references\schema.md
Test-Path docs\superpowers\specs\2026-05-24-question-image-attachments-design.md
git diff --check main...HEAD
git status --short --branch
```

Expected: each path prints `True`; `git diff --check` exits `0`; status shows
only intentional README/plan work before commit or is clean after commit.

- [ ] **Step 2: Run the existing full validation suite**

Run:

```powershell
& '<python>' -m unittest discover -s tests -v
& '<python>' -m py_compile scripts\export_review_set.py tests\test_export_review_set.py
```

Expected: all `47` current tests pass and compilation exits `0`.

- [ ] **Step 3: Commit the README implementation**

Run:

```powershell
git add README.md README.zh-CN.md docs/superpowers/plans/2026-05-25-bilingual-readme.md
git diff --cached --check
git commit -m "docs: add bilingual repository readme"
```

Expected: a commit containing only the two README pages and implementation
plan is created on `codex/add-bilingual-readme`.

- [ ] **Step 4: Push and open a draft pull request**

Run:

```powershell
git push -u origin codex/add-bilingual-readme
```

Then open a draft GitHub PR targeting `main`, with a summary of both language
documents and the validation commands executed.

Expected: the remote branch exists and the PR is open in draft state for user
review before merging.
