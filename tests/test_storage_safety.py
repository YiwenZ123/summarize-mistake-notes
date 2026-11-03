import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import CliTestCase, make_item, make_payload  # noqa: E402


class MissingStorageTests(CliTestCase):
    def config_for(self, notes_root):
        config = self.root / "config.json"
        config.write_text(json.dumps({"notes_root": str(notes_root)}), encoding="utf-8")
        return config

    def items_file(self):
        return self.write_json(
            "items.json", make_payload("Course", "Topic", [make_item("Saved", "Where is it saved?")])
        )

    def add_with_config(self, config):
        return self.run_cli(
            "db", "add", "--input", str(self.items_file()), "--confirmed-selection-by-user",
            "--config", str(config),
        )

    def test_read_only_command_does_not_recreate_missing_notes_root(self):
        missing = self.root / "OneDrive" / "data"

        stats = self.run_cli("db", "stats", "--human", "--config", str(self.config_for(missing)))

        self.assertEqual(4, stats.returncode, stats.stderr)
        self.assertIn("NOTES_ROOT_MISSING", stats.stderr)
        self.assertFalse(missing.exists())

    def test_add_does_not_recreate_missing_notes_root(self):
        missing = self.root / "moved-away"

        added = self.add_with_config(self.config_for(missing))

        self.assertEqual(4, added.returncode, added.stderr)
        self.assertIn("NOTES_ROOT_MISSING", added.stderr)
        self.assertFalse(missing.exists())

    def test_config_get_reports_missing_notes_root(self):
        missing = self.root / "unmounted"

        result = self.run_cli("config", "get", "--config", str(self.config_for(missing)))

        self.assertEqual(4, result.returncode)
        self.assertIn("NOTES_ROOT_MISSING", result.stderr)

    def test_read_only_commands_do_not_create_database_file(self):
        notes = self.root / "fresh"
        notes.mkdir()
        config = self.config_for(notes)

        for command in (
            ("db", "stats", "--human"),
            ("db", "quiz"),
            ("db", "due"),
            ("db", "search"),
            ("db", "attachment-audit"),
        ):
            result = self.run_cli(*command, "--config", str(config))
            self.assertEqual(0, result.returncode, f"{command}: {result.stderr}")
        missing_item = self.run_cli("db", "mark-done", "abc123", "--config", str(config))
        stats = self.run_cli("db", "stats", "--human", "--config", str(config))

        self.assertIn("Question not found", missing_item.stderr)
        self.assertIn("No courses", stats.stdout)
        self.assertEqual([], list(notes.iterdir()))

    def test_first_add_creates_database_in_existing_notes_root(self):
        notes = self.root / "fresh"
        notes.mkdir()

        added = self.add_with_config(self.config_for(notes))

        self.assertEqual(0, added.returncode, added.stderr)
        self.assertTrue((notes / "exercise_bank.sqlite3").exists())

    def test_missing_database_beside_existing_attachments_is_reported(self):
        notes = self.root / "still-syncing"
        (notes / "attachments").mkdir(parents=True)
        config = self.config_for(notes)

        stats = self.run_cli("db", "stats", "--human", "--config", str(config))
        added = self.add_with_config(config)

        self.assertEqual(4, stats.returncode, stats.stderr)
        self.assertIn("NOTES_DB_MISSING", stats.stderr)
        self.assertEqual(4, added.returncode, added.stderr)
        self.assertFalse((notes / "exercise_bank.sqlite3").exists())
