import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


PYTHON = Path(r"C:\Users\Zippe\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
SCRIPT = Path(r"C:\Users\Zippe\.codex\skills\summarize-mistake-notes\scripts\export_review_set.py")
SKILL = Path(r"C:\Users\Zippe\.codex\skills\summarize-mistake-notes\SKILL.md")


class AddQuestionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "exercise_bank.sqlite3"
        self.input_path = Path(self.temp_dir.name) / "items.json"
        self.input_path.write_text(
            json.dumps(
                {
                    "course": "Test Course",
                    "collection_type": "question_set",
                    "topic": "Selection",
                    "items": [
                        {
                            "title": "Candidate item",
                            "original_question": "Should this be saved?",
                            "knowledge_points": ["Selection"],
                            "mistake_reason": "Needs review",
                            "correct_approach": "Require user choice.",
                            "answer_points": ["Save only chosen items."],
                            "review_suggestion": "Select an item explicitly.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [str(PYTHON), str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_add_requires_explicit_user_selection(self):
        added = self.run_cli("db", "add", "--input", str(self.input_path), "--db-path", str(self.db_path))

        self.assertNotEqual(0, added.returncode)
        self.assertIn("explicit user selection", added.stderr)

    def test_add_accepts_only_after_explicit_user_selection(self):
        added = self.run_cli(
            "db",
            "add",
            "--input",
            str(self.input_path),
            "--confirmed-selection-by-user",
            "--db-path",
            str(self.db_path),
        )

        self.assertEqual(0, added.returncode, added.stderr)
        self.assertEqual(1, json.loads(added.stdout)["inserted"])


class AddQuestionSelectionInstructionsTests(unittest.TestCase):
    def test_add_requires_candidate_preview_and_explicit_selection(self):
        instructions = SKILL.read_text(encoding="utf-8")

        self.assertIn("Candidate items:", instructions)
        self.assertIn(
            "Do not write any item until the user explicitly selects which numbered candidates to save",
            instructions,
        )
        self.assertIn('db add --input "<prepared-json-file>" --confirmed-selection-by-user', instructions)


class ExportQuestionsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "exercise_bank.sqlite3"
        for course, topic, title, prompt, answer in (
            ("Course A", "Topic One", "A One", "Prompt A1?", "Secret answer A1"),
            ("Course A", "Topic Two", "A Two", "Prompt A2?", "Secret answer A2"),
            ("Course B", "Topic One", "B One", "Prompt B1?", "Secret answer B1"),
        ):
            input_path = self.root / f"{title.replace(' ', '-')}.json"
            input_path.write_text(
                json.dumps(
                    {
                        "course": course,
                        "collection_type": "question_set",
                        "topic": topic,
                        "items": [
                            {
                                "title": title,
                                "original_question": prompt,
                                "knowledge_points": ["Export testing"],
                                "mistake_reason": "Needs review",
                                "correct_approach": "Use the saved solution.",
                                "answer_points": [answer],
                                "review_suggestion": "Review it.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            added = self.run_cli(
                "db",
                "add",
                "--input",
                str(input_path),
                "--confirmed-selection-by-user",
                "--db-path",
                str(self.db_path),
            )
            self.assertEqual(0, added.returncode, added.stderr)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [str(PYTHON), str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def exported_content(self, command):
        self.assertEqual(0, command.returncode, command.stderr)
        result = json.loads(command.stdout)
        path = Path(result["markdown_path"])
        self.assertTrue(path.exists())
        self.assertEqual(self.root / "exports", path.parent)
        return result, path.read_text(encoding="utf-8")

    def test_export_full_by_course_contains_only_questions_and_answers(self):
        result, content = self.exported_content(
            self.run_cli(
                "db",
                "export",
                "--course",
                "Course A",
                "--mode",
                "full",
                "--notes-root",
                str(self.root),
                "--db-path",
                str(self.db_path),
            )
        )

        self.assertEqual(2, result["item_count"])
        self.assertIn("A One", content)
        self.assertIn("A Two", content)
        self.assertNotIn("B One", content)
        self.assertIn("Secret answer A1", content)
        self.assertIn("### 问题", content)
        self.assertIn("### 回答", content)
        self.assertNotIn("Export date", content)
        self.assertNotIn("Course filter", content)
        self.assertNotIn("Knowledge Points", content)
        self.assertNotIn("Correct Approach", content)
        self.assertNotIn("Review Suggestion", content)
        self.assertNotIn("Review Status", content)
        self.assertNotIn("- Course:", content)
        self.assertNotIn("- Topic:", content)

    def test_export_questions_only_by_topic_hides_answer_content(self):
        result, content = self.exported_content(
            self.run_cli(
                "db",
                "export",
                "--topic",
                "Topic One",
                "--mode",
                "questions-only",
                "--notes-root",
                str(self.root),
                "--db-path",
                str(self.db_path),
            )
        )

        self.assertEqual(2, result["item_count"])
        self.assertIn("Prompt A1?", content)
        self.assertIn("Prompt B1?", content)
        self.assertNotIn("Secret answer", content)
        self.assertIn("### 问题", content)
        self.assertNotIn("### 回答", content)
        self.assertNotIn("Knowledge Points", content)
        self.assertNotIn("Review Status", content)
        self.assertNotIn("- Course:", content)
        self.assertNotIn("- Topic:", content)

    def test_export_course_and_topic_filters_are_combined(self):
        result, content = self.exported_content(
            self.run_cli(
                "db",
                "export",
                "--course",
                "Course A",
                "--topic",
                "Topic One",
                "--mode",
                "full",
                "--notes-root",
                str(self.root),
                "--db-path",
                str(self.db_path),
            )
        )

        self.assertEqual(1, result["item_count"])
        self.assertIn("A One", content)
        self.assertNotIn("A Two", content)
        self.assertNotIn("B One", content)

    def test_export_requires_course_or_topic_filter(self):
        exported = self.run_cli(
            "db",
            "export",
            "--mode",
            "full",
            "--notes-root",
            str(self.root),
            "--db-path",
            str(self.db_path),
        )

        self.assertNotEqual(0, exported.returncode)
        self.assertIn("At least one of --course or --topic is required", exported.stderr)
        self.assertFalse((self.root / "exports").exists())

    def test_export_with_no_matches_does_not_create_file(self):
        exported = self.run_cli(
            "db",
            "export",
            "--course",
            "Missing Course",
            "--mode",
            "questions-only",
            "--notes-root",
            str(self.root),
            "--db-path",
            str(self.db_path),
        )

        self.assertNotEqual(0, exported.returncode)
        self.assertIn("No questions matched the export filters", exported.stderr)
        self.assertFalse((self.root / "exports").exists())


class ExportQuestionsInstructionsTests(unittest.TestCase):
    def test_export_prompts_for_scope_and_output_mode(self):
        instructions = SKILL.read_text(encoding="utf-8")

        self.assertIn('db export --course "<course>" --mode full', instructions)
        self.assertIn('db export --topic "<topic>" --mode questions-only', instructions)
        self.assertIn("Ask whether to export `full` or `questions-only`", instructions)
        self.assertIn("Do not export all questions when neither a course nor a topic is specified", instructions)
        self.assertIn("A `full` export contains only each question title, `### 问题`, and `### 回答`", instructions)
        self.assertIn("Do not include database metadata, review status, knowledge points, correct approach, or review suggestion", instructions)


class DeleteQuestionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "exercise_bank.sqlite3"
        self.input_path = Path(self.temp_dir.name) / "items.json"
        self.input_path.write_text(
            json.dumps(
                {
                    "course": "Test Course",
                    "collection_type": "question_set",
                    "topic": "Deletion",
                    "items": [
                        {
                            "title": "Remove me",
                            "original_question": "Question text?",
                            "knowledge_points": ["Deletion"],
                            "mistake_reason": "Needs review",
                            "correct_approach": "Answer it.",
                            "answer_points": ["One point"],
                            "review_suggestion": "Try again.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        add = self.run_cli(
            "db",
            "add",
            "--input",
            str(self.input_path),
            "--confirmed-selection-by-user",
            "--db-path",
            str(self.db_path),
        )
        self.assertEqual(0, add.returncode, add.stderr)
        self.item_id = json.loads(add.stdout)["ids"][0]

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [str(PYTHON), str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_delete_removes_question_and_its_review_events(self):
        marked = self.run_cli("db", "mark-wrong", self.item_id, "--db-path", str(self.db_path))
        self.assertEqual(0, marked.returncode, marked.stderr)

        deleted = self.run_cli("db", "delete", self.item_id, "--db-path", str(self.db_path))

        self.assertEqual(0, deleted.returncode, deleted.stderr)
        self.assertEqual(self.item_id, json.loads(deleted.stdout)["deleted_item"]["id"])
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0])
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM review_events").fetchone()[0])
        finally:
            connection.close()

    def test_delete_missing_question_fails(self):
        deleted = self.run_cli("db", "delete", "missing-item", "--db-path", str(self.db_path))

        self.assertNotEqual(0, deleted.returncode)
        self.assertIn("Question not found: missing-item", deleted.stderr)


class DeleteAuthorizationInstructionsTests(unittest.TestCase):
    def test_delete_requires_explicit_user_approval(self):
        instructions = SKILL.read_text(encoding="utf-8")

        self.assertIn("db delete <item_id>", instructions)
        self.assertIn(
            "Only run deletion after the user explicitly requests permanent deletion or confirms it",
            instructions,
        )


class RenameCourseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "exercise_bank.sqlite3"
        self.input_path = Path(self.temp_dir.name) / "items.json"
        self.input_path.write_text(
            json.dumps(
                {
                    "course": "Old Course",
                    "collection_type": "question_set",
                    "topic": "Renaming",
                    "items": [
                        {
                            "title": "Reviewed item",
                            "original_question": "What remains reviewed?",
                            "knowledge_points": ["Course organization"],
                            "mistake_reason": "Needs review",
                            "correct_approach": "Preserve history.",
                            "answer_points": ["The state remains done."],
                            "review_suggestion": "Check status.",
                        },
                        {
                            "title": "Pending item",
                            "original_question": "What remains pending?",
                            "knowledge_points": ["Course organization"],
                            "mistake_reason": "Needs review",
                            "correct_approach": "Preserve history.",
                            "answer_points": ["The state remains pending."],
                            "review_suggestion": "Check status.",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        add = self.run_cli(
            "db",
            "add",
            "--input",
            str(self.input_path),
            "--confirmed-selection-by-user",
            "--db-path",
            str(self.db_path),
        )
        self.assertEqual(0, add.returncode, add.stderr)
        self.item_ids = json.loads(add.stdout)["ids"]
        done = self.run_cli("db", "mark-done", self.item_ids[0], "--db-path", str(self.db_path))
        wrong = self.run_cli(
            "db",
            "mark-wrong",
            self.item_ids[1],
            "--note",
            "Keep this event",
            "--db-path",
            str(self.db_path),
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(0, wrong.returncode, wrong.stderr)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [str(PYTHON), str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_rename_requires_explicit_user_confirmation(self):
        rename = self.run_cli(
            "db", "rename-course", "Old Course", "New Course", "--db-path", str(self.db_path)
        )

        self.assertNotEqual(0, rename.returncode)
        self.assertIn("explicit user confirmation", rename.stderr)
        old = self.run_cli("db", "search", "--course", "Old Course", "--db-path", str(self.db_path))
        self.assertEqual(2, json.loads(old.stdout)["returned_count"])

    def test_rename_preserves_items_and_review_history(self):
        rename = self.run_cli(
            "db",
            "rename-course",
            "Old Course",
            "New Course",
            "--confirmed-by-user",
            "--db-path",
            str(self.db_path),
        )

        self.assertEqual(0, rename.returncode, rename.stderr)
        result = json.loads(rename.stdout)
        self.assertEqual("Old Course", result["old_course"])
        self.assertEqual("New Course", result["new_course"])
        self.assertEqual(2, result["renamed_questions"])

        new = self.run_cli(
            "db", "search", "--course", "New Course", "--limit", "10", "--db-path", str(self.db_path)
        )
        items = json.loads(new.stdout)["items"]
        self.assertEqual(self.item_ids, [item["id"] for item in items])
        self.assertEqual(["done", "pending"], [item["review_status"] for item in items])
        self.assertEqual([1, 1], [item["review_count"] + item["wrong_count"] for item in items])

        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM review_events").fetchone()[0])
            indexed = connection.execute(
                "SELECT search_text FROM questions ORDER BY source_date, created_at, id"
            ).fetchall()
            self.assertTrue(all(row[0].startswith("New Course\n") for row in indexed))
        finally:
            connection.close()

    def test_rename_does_not_merge_into_existing_course(self):
        second_input = Path(self.temp_dir.name) / "target.json"
        second_input.write_text(
            json.dumps(
                {
                    "course": "New Course",
                    "collection_type": "question_set",
                    "topic": "Target",
                    "items": [
                        {
                            "title": "Existing target",
                            "original_question": "Already here?",
                            "knowledge_points": ["Target"],
                            "mistake_reason": "Needs review",
                            "correct_approach": "Keep separate.",
                            "answer_points": ["Do not merge automatically."],
                            "review_suggestion": "Confirm scope.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        added = self.run_cli(
            "db",
            "add",
            "--input",
            str(second_input),
            "--confirmed-selection-by-user",
            "--db-path",
            str(self.db_path),
        )
        self.assertEqual(0, added.returncode, added.stderr)

        rename = self.run_cli(
            "db",
            "rename-course",
            "Old Course",
            "New Course",
            "--confirmed-by-user",
            "--db-path",
            str(self.db_path),
        )

        self.assertNotEqual(0, rename.returncode)
        self.assertIn("Target course already exists", rename.stderr)


class RenameAuthorizationInstructionsTests(unittest.TestCase):
    def test_rename_requires_explicit_user_approval(self):
        instructions = SKILL.read_text(encoding="utf-8")

        self.assertIn('db rename-course "<old-course>" "<new-course>" --confirmed-by-user', instructions)
        self.assertIn(
            "Only run a course rename after the user explicitly confirms the exact old and new course names",
            instructions,
        )


if __name__ == "__main__":
    unittest.main()
