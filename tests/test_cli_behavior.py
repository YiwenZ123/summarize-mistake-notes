import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    PYTHON,
    SCRIPT,
    CliTestCase,
    load_script_module,
    make_item,
    make_payload,
    subcommands,
    write_image,
)


BLOCK_PILLOW = (
    "import runpy, sys; sys.modules['PIL'] = None; sys.argv = sys.argv[1:]; "
    "runpy.run_path(sys.argv[0], run_name='__main__')"
)


class ExitCodeTests(CliTestCase):
    def test_not_configured_uses_exit_code_distinct_from_usage_errors(self):
        not_configured = self.run_cli("db", "stats", "--config", str(self.root / "missing.json"))
        usage_error = self.run_cli("db", "stats", "--unknown-flag")

        self.assertEqual(3, not_configured.returncode, not_configured.stderr)
        self.assertIn("NOT_CONFIGURED", not_configured.stderr)
        self.assertEqual(2, usage_error.returncode)

    def test_config_get_without_config_reports_not_configured(self):
        result = self.run_cli("config", "get", "--config", str(self.root / "missing.json"))

        self.assertEqual(3, result.returncode)
        self.assertIn("NOT_CONFIGURED", result.stderr)

    def test_every_db_command_accepts_storage_options(self):
        db_parser = subcommands(load_script_module().build_parser())["db"]

        for name, command in subcommands(db_parser).items():
            options = {option for action in command._actions for option in action.option_strings}
            self.assertLessEqual({"--config", "--db-path", "--notes-root"}, options, name)

    def test_filesystem_error_is_reported_without_traceback(self):
        self.add_items(make_item("Blocked export", "Where does the export go?"), course="Course")
        (self.root / "exports").write_text("a file where the exports folder belongs", encoding="utf-8")

        exported = self.run_db("export", "--course", "Course", "--mode", "full")

        self.assertEqual(1, exported.returncode)
        self.assertIn("exports", exported.stderr)
        self.assertNotIn("Traceback", exported.stderr)


class PillowOptionalTests(CliTestCase):
    def run_without_pillow(self, *args):
        return subprocess.run(
            [str(PYTHON), "-c", BLOCK_PILLOW, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_text_only_workflow_runs_without_pillow(self):
        input_path = self.write_json(
            "text-only.json", make_payload("Course", "Topic", [make_item("Text", "Plain question?")])
        )
        db = ("--db-path", str(self.db_path))

        added = self.run_without_pillow(
            "db", "add", "--input", str(input_path), "--confirmed-selection-by-user", *db
        )
        self.assertEqual(0, added.returncode, added.stderr)
        item_id = json.loads(added.stdout)["ids"][0]
        for command in (
            ("db", "stats", "--human"),
            ("db", "quiz", "--course", "Course"),
            ("db", "search", "--include-content"),
            ("db", "mark-wrong", item_id, "--note", "Missed a step."),
        ):
            result = self.run_without_pillow(*command, *db)
            self.assertEqual(0, result.returncode, f"{command}: {result.stderr}")

    def test_attachment_without_pillow_reports_missing_dependency(self):
        item_id = self.add_items(make_item("Figure", "Read the figure?"))[0]
        image = write_image(self.root / "figure.png")

        attached = self.run_without_pillow(
            "db", "attach", item_id, "--source", str(image), "--role", "prompt",
            "--provenance", "provided", "--caption", "Figure", "--db-path", str(self.db_path),
        )

        self.assertEqual(1, attached.returncode)
        self.assertIn("PILLOW_REQUIRED", attached.stderr)
        self.assertNotIn("Traceback", attached.stderr)
        self.assertFalse((self.root / "attachments").exists())
