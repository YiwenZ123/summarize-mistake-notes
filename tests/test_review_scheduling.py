import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import CliTestCase, load_script_module, make_item  # noqa: E402


def day(offset):
    return (date.today() + timedelta(days=offset)).isoformat()


class GradeSchedulingTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.item_id = self.add_items(
            make_item("Stage", "Which stage comes next?"), course="Physics", collection_type="mistake_set"
        )[0]

    def schedule(self):
        rows = self.query_db(
            "SELECT review_status, review_stage, next_review_at FROM questions WHERE id = ?",
            (self.item_id,),
        )
        return tuple(rows[0])

    def make_due(self, stage=None):
        if stage is None:
            self.query_db("UPDATE questions SET next_review_at = ? WHERE id = ?", (day(-1), self.item_id))
        else:
            self.query_db(
                "UPDATE questions SET next_review_at = ?, review_stage = ? WHERE id = ?",
                (day(-1), stage, self.item_id),
            )

    def grade(self, result, *extra):
        return self.db_json("grade", self.item_id, "--result", result, *extra)

    def due_ids(self):
        return [item["id"] for item in self.db_json("due", "--limit", "10")["items"]]

    def test_new_item_is_due_immediately(self):
        due = self.db_json("due")["items"]

        self.assertEqual([self.item_id], [item["id"] for item in due])
        self.assertTrue(due[0]["due"])
        self.assertEqual(0, due[0]["review_stage"])

    def test_correct_answer_schedules_next_review_and_leaves_due_list(self):
        graded = self.grade("correct")

        self.assertTrue(graded["schedule"]["advanced"])
        self.assertEqual(("pending", 1, day(1)), self.schedule())
        self.assertEqual([], self.due_ids())
        quiz = self.db_json("quiz")["items"]
        self.assertEqual([self.item_id], [item["id"] for item in quiz])
        self.assertFalse(quiz[0]["due"])

    def test_correct_answer_before_due_date_does_not_advance(self):
        self.grade("correct")

        practice = self.grade("correct")

        self.assertFalse(practice["schedule"]["advanced"])
        self.assertEqual(("pending", 1, day(1)), self.schedule())
        self.assertEqual(2, practice["item"]["review_count"])

    def test_correct_answers_follow_interval_sequence_until_mastered(self):
        intervals = []
        for _ in range(5):
            self.make_due()
            self.grade("correct")
            next_review = self.schedule()[2]
            intervals.append((date.fromisoformat(next_review) - date.today()).days)
        self.make_due()

        mastered = self.grade("correct")

        self.assertEqual([1, 3, 7, 14, 30], intervals)
        self.assertTrue(mastered["schedule"]["mastered"])
        self.assertEqual(("done", 5, None), self.schedule())
        course = self.db_json("stats")["courses"][0]
        self.assertEqual((1, 0, 0), (course["done"], course["pending"], course["due"]))

    def test_wrong_answer_resets_stage_and_postpones_to_tomorrow(self):
        self.make_due(stage=3)

        graded = self.grade("wrong", "--note", "Mixed up the sign.")

        self.assertEqual(("pending", 0, day(1)), self.schedule())
        self.assertEqual(1, graded["item"]["wrong_count"])
        self.assertEqual([], self.due_ids())

    def test_partial_answer_keeps_stage_and_reviews_tomorrow(self):
        self.make_due(stage=2)

        self.grade("partial", "--note", "Forgot the units.")

        self.assertEqual(("pending", 2, day(1)), self.schedule())

    def test_wrong_answer_on_mastered_item_returns_it_to_review(self):
        self.db_json("mark-done", self.item_id)

        self.grade("wrong")

        self.assertEqual(("pending", 0, day(1)), self.schedule())

    def test_legacy_mark_commands_keep_their_meaning(self):
        self.db_json("mark-wrong", self.item_id, "--note", "Legacy command.")
        after_wrong = self.schedule()
        self.db_json("mark-done", self.item_id)

        self.assertEqual(("pending", 0, day(1)), after_wrong)
        self.assertEqual("done", self.schedule()[0])
        self.assertIsNone(self.schedule()[2])

    def test_human_stats_show_due_pending_and_total(self):
        self.grade("correct")

        stats = self.run_db("stats", "--human")

        self.assertIn("1. Physics (0 due / 1 pending / 1 total)", stats.stdout)


class ReviewOrderingTests(CliTestCase):
    def test_due_lists_most_overdue_first_and_shuffle_returns_same_items(self):
        first, second, third = self.add_items(
            make_item("A", "QA?"), make_item("B", "QB?"), make_item("C", "QC?")
        )
        self.query_db("UPDATE questions SET next_review_at = '2000-01-02' WHERE id = ?", (first,))
        self.query_db("UPDATE questions SET next_review_at = '2000-01-01' WHERE id = ?", (third,))

        ordered = [item["id"] for item in self.db_json("due", "--limit", "10")["items"]]
        shuffled = [item["id"] for item in self.db_json("due", "--limit", "10", "--shuffle")["items"]]

        self.assertEqual([third, first, second], ordered)
        self.assertEqual(sorted(ordered), sorted(shuffled))

    def test_quiz_puts_due_items_first_and_can_include_mastered_items(self):
        scheduled, due, mastered = self.add_items(
            make_item("A", "QA?"), make_item("B", "QB?"), make_item("C", "QC?")
        )
        self.query_db("UPDATE questions SET next_review_at = ? WHERE id = ?", (day(5), scheduled))
        self.db_json("mark-done", mastered)

        quiz = [item["id"] for item in self.db_json("quiz", "--limit", "10")["items"]]
        everything = [
            item["id"] for item in self.db_json("quiz", "--limit", "10", "--include-done")["items"]
        ]

        self.assertEqual([due, scheduled], quiz)
        self.assertEqual([due, scheduled, mastered], everything)


class ShowAndHistoryTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.item_id = self.add_items(
            make_item("Units", "Convert 3 km to metres.", answer_points=["3000 m"])
        )[0]
        self.db_json("grade", self.item_id, "--result", "wrong", "--note", "Multiplied by 100.")

    def test_show_returns_saved_answer_and_review_history(self):
        shown = self.db_json("show", self.item_id)["item"]

        self.assertEqual(["3000 m"], shown["answer_points"])
        self.assertIn("3000 m", shown["content"])
        self.assertEqual("wrong", shown["history"][0]["result"])
        self.assertEqual("Multiplied by 100.", shown["history"][0]["note"])

    def test_quiz_output_hides_answers_and_review_notes(self):
        self.query_db("UPDATE questions SET next_review_at = NULL WHERE id = ?", (self.item_id,))

        quiz = self.run_db("quiz")

        self.assertEqual(0, quiz.returncode, quiz.stderr)
        self.assertIn("Convert 3 km to metres.", quiz.stdout)
        self.assertNotIn("3000 m", quiz.stdout)
        self.assertNotIn("Multiplied by 100.", quiz.stdout)

    def test_show_missing_item_fails(self):
        result = self.run_db("show", "missing-item")

        self.assertEqual(1, result.returncode)
        self.assertIn("Question not found", result.stderr)


class ResetTests(CliTestCase):
    def test_reset_requires_confirmation_and_previews_scope(self):
        mastered, scheduled = self.add_items(make_item("A", "QA?"), make_item("B", "QB?"), course="Exam")
        self.db_json("mark-done", mastered)
        self.db_json("grade", scheduled, "--result", "correct")

        preview = self.run_db("reset", "--course", "Exam")

        self.assertEqual(5, preview.returncode)
        self.assertIn("explicit user confirmation", preview.stderr)
        self.assertEqual(2, json.loads(preview.stdout)["preview"]["item_count"])
        self.assertEqual(1, self.db_json("stats")["courses"][0]["done"])

        reset = self.db_json("reset", "--course", "Exam", "--confirmed-by-user")

        self.assertEqual(2, reset["reset_count"])
        due = [item["id"] for item in self.db_json("due", "--limit", "10")["items"]]
        self.assertEqual(sorted([mastered, scheduled]), sorted(due))

    def test_reset_requires_a_scope(self):
        self.add_items(make_item("A", "QA?"))

        result = self.run_db("reset", "--confirmed-by-user")

        self.assertEqual(1, result.returncode)
        self.assertIn("--course", result.stderr)


LEGACY_SCHEMA = """
CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE questions (
    id TEXT PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    collection_type TEXT NOT NULL,
    topic TEXT NOT NULL,
    title TEXT NOT NULL,
    original_question TEXT NOT NULL,
    knowledge_points_json TEXT NOT NULL,
    mistake_reason TEXT NOT NULL,
    correct_approach TEXT NOT NULL,
    answer_points_json TEXT NOT NULL,
    review_suggestion TEXT NOT NULL,
    source_date TEXT NOT NULL,
    search_text TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending',
    reviewed_at TEXT,
    review_count INTEGER NOT NULL DEFAULT 0,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE review_events (
    id INTEGER PRIMARY KEY,
    question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class MigrationTests(CliTestCase):
    def create_legacy_database(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript(LEGACY_SCHEMA)
            connection.execute("INSERT INTO courses VALUES (1, 'Legacy', '2026-05-01T00:00:00')")
            for item_id, status in (("pending001", "pending"), ("mastered01", "done")):
                connection.execute(
                    """
                    INSERT INTO questions VALUES (
                        ?, 1, 'mistake_set', 'Old topic', ?, ?, '["k"]', 'r', 'c', '["a"]', 's',
                        '2026-05-01', 'Legacy', ?, NULL, 0, 0,
                        '2026-05-01T00:00:00', '2026-05-01T00:00:00'
                    )
                    """,
                    (item_id, f"Title {item_id}", f"Question {item_id}?", status),
                )
            connection.execute(
                "INSERT INTO review_events(question_id, result, created_at) "
                "VALUES ('mastered01', 'correct', '2026-05-02T00:00:00')"
            )
            connection.commit()
        finally:
            connection.close()

    def test_legacy_database_is_migrated_without_losing_review_state(self):
        self.create_legacy_database()

        course = self.db_json("stats")["courses"][0]

        self.assertEqual(
            ("Legacy", 2, 1, 1, 1),
            (course["course"], course["total"], course["pending"], course["done"], course["due"]),
        )
        self.assertEqual(
            load_script_module().SCHEMA_VERSION, self.query_db("PRAGMA user_version")[0][0]
        )
        self.assertEqual(["pending001"], [item["id"] for item in self.db_json("due")["items"]])
        self.assertEqual([("correct", None)], self.query_db("SELECT result, note FROM review_events"))

    def test_legacy_course_rename_rebuilds_search_text(self):
        self.create_legacy_database()

        renamed = self.run_db("rename-course", "Legacy", "Renamed", "--confirmed-by-user")

        self.assertEqual(0, renamed.returncode, renamed.stderr)
        self.assertEqual(2, self.db_json("search", "--query", "Renamed")["returned_count"])

    def test_database_from_newer_skill_version_is_rejected(self):
        self.add_items(make_item("A", "QA?"))
        self.query_db("PRAGMA user_version = 99")

        result = self.run_db("stats")

        self.assertEqual(1, result.returncode)
        self.assertIn("newer version", result.stderr)
