import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    README,
    README_ZH_CN,
    SCHEMA_REFERENCE,
    SKILL,
    load_script_module,
    subcommands,
)


class ReviewInstructionsTests(unittest.TestCase):
    def setUp(self):
        self.instructions = SKILL.read_text(encoding="utf-8")

    def test_review_flow_routes_courses_topics_and_keywords_to_matching_filters(self):
        for command in (
            'db quiz --course "<course>"',
            'db quiz --topic "<topic>"',
            'db quiz --query "<keyword>"',
            "db due --limit 3",
            "db due --shuffle",
            "db stats --human --topics",
        ):
            self.assertIn(command, self.instructions)
        self.assertNotIn('db quiz --course "<user answer>"', self.instructions)

    def test_review_flow_reads_saved_answer_only_after_user_answers(self):
        self.assertIn("db show <item_id>", self.instructions)
        self.assertIn("Never run `db show` before the user has answered", self.instructions)

    def test_review_flow_grades_with_schedule_aware_command(self):
        self.assertIn("db grade <item_id> --result <correct|partial|wrong>", self.instructions)
        self.assertIn("`mark-done` masters an item permanently", self.instructions)

    def test_missing_storage_is_reported_instead_of_recreated(self):
        self.assertIn("NOTES_ROOT_MISSING", self.instructions)
        self.assertIn("NOTES_DB_MISSING", self.instructions)
        self.assertIn("Never create a replacement folder or database", self.instructions)

    def test_reset_and_update_require_confirmation(self):
        self.assertIn('db reset --course "<course>" --confirmed-by-user', self.instructions)
        self.assertIn(
            'db update <item_id> --input "<changes-json-file>" --confirmed-by-user', self.instructions
        )

    def test_review_flow_defines_grading_skips_language_and_option_placement(self):
        for phrase in (
            "in the learner's language",
            "go after the full subcommand",
            "`wrong` means the key result is wrong or missing",
            "asks to stop reviewing an item instead of answering it",
            "db quiz --shuffle",
            "<python> <script> db mark-done <item_id>",
            "If the request already names a scope",
            "returned fewer items than `--limit`",
        ):
            self.assertIn(phrase, self.instructions)

    def test_paths_use_forward_slashes(self):
        self.assertNotIn("\\exports", self.instructions)


class DocumentationConsistencyTests(unittest.TestCase):
    def test_both_readmes_document_every_db_command(self):
        db_parser = subcommands(load_script_module().build_parser())["db"]
        for path in (README, README_ZH_CN):
            content = path.read_text(encoding="utf-8")
            for name in subcommands(db_parser):
                self.assertIn(f"`db {name}`", content, f"{path.name} does not document db {name}")

    def test_both_readmes_document_exit_codes(self):
        for path in (README, README_ZH_CN):
            content = path.read_text(encoding="utf-8")
            for token in ("NOT_CONFIGURED", "NOTES_ROOT_MISSING", "NOTES_DB_MISSING", "PILLOW_REQUIRED"):
                self.assertIn(token, content, f"{path.name}: {token}")

    def test_schema_reference_documents_update_input(self):
        schema = SCHEMA_REFERENCE.read_text(encoding="utf-8")

        self.assertIn("db update", schema)
        for field in ("course", "topic", "answer_points"):
            self.assertIn(f"`{field}`", schema)
