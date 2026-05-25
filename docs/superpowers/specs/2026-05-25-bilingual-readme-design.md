# Bilingual GitHub README

Date: 2026-05-25
Status: Approved structure, ready for implementation review

## Goal

Add a complete public-facing introduction for the `summarize-mistake-notes`
repository so GitHub visitors can understand, install, use, validate, and
contribute to the skill without first reading its internal operating
instructions.

The documentation will be available in both English and Simplified Chinese.

## File Strategy

- `README.md` is the default English landing page for the public GitHub
  repository.
- `README.zh-CN.md` is the corresponding Simplified Chinese version.
- Both documents begin with a prominent language switch link.
- The two documents cover the same feature set and workflows; translation may
  adapt phrasing, but must not omit safety requirements or change command
  meaning.

## Intended Readers

The README serves three audiences:

- learners who want an exercise bank with guided review workflows;
- Codex users who want to install and invoke the skill;
- contributors who need enough repository and validation context to change it
  responsibly.

It is not a substitute for `SKILL.md`, which remains the operational
instruction source used by Codex.

## Content Outline

Each language version will contain:

1. Project purpose and key capabilities.
2. A concise explanation of the SQLite-first storage model and Markdown
   exports.
3. Repository structure.
4. Requirements and installation/setup guidance.
5. Configuration of the managed notes root and database path.
6. Typical usage workflows and representative command examples:
   adding items, searching, reviewing, exporting, attaching images, and
   auditing attachments.
7. Managed image attachment behavior, including `prompt` versus `solution`
   visibility and `provided` versus `reconstructed` provenance.
8. Safety and privacy boundaries.
9. JSON input example and field reference pointer.
10. Test and validation commands.
11. Contribution and GitHub branch/PR workflow.

## Documentation Boundaries

- Commands and option names will be taken from the current script and
  `SKILL.md`, not invented for readability.
- Installation guidance will distinguish repository installation from the
  current author's machine-specific fixed runtime paths in `SKILL.md`.
- The README will not imply that the local exercise database or managed user
  images are committed to Git.
- Examples will use generic locations and example data, not the user's real
  notes root or stored study materials.
- The README will explain that database writes should go through the script
  and that destructive attachment operations require explicit confirmation.

## Accuracy And Validation

Before publication:

- inspect CLI help for commands cited in the README;
- confirm all linked repository files and language links exist;
- run Markdown-oriented diff checks and the existing test suite so the
  documentation ships alongside a known-good repository state;
- publish through a feature branch and pull request targeted at `main`.

## Deliverables

- `README.md`
- `README.zh-CN.md`
- this design record, retained in repository history as documentation rationale
