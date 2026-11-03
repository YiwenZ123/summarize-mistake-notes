import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import CliTestCase, make_item, make_payload  # noqa: E402


class UpdateItemTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.item_id = self.add_items(
            make_item("Power rule", "Waht is the derivative of x^2?"),
            course="Calculus",
            topic="Derivatives",
        )[0]
        self.db_json("grade", self.item_id, "--result", "wrong", "--note", "Forgot the power rule.")

    def update(self, changes, *extra):
        changes_path = self.write_json("changes.json", changes)
        return self.run_db("update", self.item_id, "--input", str(changes_path), *extra)

    def test_update_requires_confirmation_and_previews_changes(self):
        result = self.update({"original_question": "What is the derivative of x^2?"})

        self.assertEqual(5, result.returncode)
        self.assertIn("explicit user confirmation", result.stderr)
        self.assertEqual(
            {"from": "Waht is the derivative of x^2?", "to": "What is the derivative of x^2?"},
            json.loads(result.stdout)["preview"]["changes"]["original_question"],
        )
        self.assertEqual(
            "Waht is the derivative of x^2?", self.db_json("search")["items"][0]["original_question"]
        )

    def test_update_fixes_question_in_place_and_keeps_review_history(self):
        result = self.update({"original_question": "What is the derivative of x^2?"}, "--confirmed-by-user")

        self.assertEqual(0, result.returncode, result.stderr)
        items = self.db_json("search", "--query", "What is the derivative")["items"]
        self.assertEqual([self.item_id], [item["id"] for item in items])
        self.assertEqual(1, items[0]["wrong_count"])
        self.assertEqual(1, self.db_json("search")["returned_count"])
        resaved = self.add_payload(
            make_payload(
                "Calculus", "Derivatives", [make_item("Power rule", "What is the derivative of x^2?")]
            )
        )
        self.assertEqual([self.item_id], resaved["ids"])

    def test_update_moves_item_to_another_course_and_removes_empty_course(self):
        result = self.update({"course": "Mathematics", "topic": "Power rule"}, "--confirmed-by-user")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["Mathematics"], [course["course"] for course in self.db_json("stats")["courses"]])
        self.assertEqual([("Mathematics",)], self.query_db("SELECT name FROM courses"))
        self.assertEqual(1, self.db_json("search", "--query", "Mathematics")["returned_count"])

    def test_update_rejects_collision_with_existing_item(self):
        self.add_items(make_item("Other", "Other question?"), course="Calculus", topic="Derivatives")

        result = self.update({"title": "Other", "original_question": "Other question?"}, "--confirmed-by-user")

        self.assertEqual(1, result.returncode)
        self.assertIn("already exists", result.stderr)

    def test_update_rejects_invalid_or_unchanged_fields(self):
        empty_list = self.update({"answer_points": []}, "--confirmed-by-user")
        unknown = self.update({"review_status": "done"}, "--confirmed-by-user")
        unchanged = self.update({"title": "Power rule"}, "--confirmed-by-user")

        self.assertEqual(1, empty_list.returncode)
        self.assertIn("answer_points", empty_list.stderr)
        self.assertEqual(1, unknown.returncode)
        self.assertIn("review_status", unknown.stderr)
        self.assertEqual(1, unchanged.returncode)
        self.assertIn("does not change", unchanged.stderr)


class ConfirmationPreviewTests(CliTestCase):
    def test_delete_without_confirmation_previews_the_item(self):
        item_id = self.add_items(make_item("Keep me", "Still here?"))[0]

        result = self.run_db("delete", item_id)

        self.assertEqual(5, result.returncode)
        self.assertEqual("Keep me", json.loads(result.stdout)["preview"]["title"])
        self.assertEqual(1, self.db_json("search")["returned_count"])

    def test_rename_without_confirmation_previews_affected_count(self):
        self.add_items(make_item("A", "QA?"), make_item("B", "QB?"), course="Old")

        result = self.run_db("rename-course", "Old", "New")

        self.assertEqual(5, result.returncode)
        self.assertEqual(
            {"old_course": "Old", "new_course": "New", "question_count": 2},
            json.loads(result.stdout)["preview"],
        )
