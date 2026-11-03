import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import CliTestCase, make_item, make_payload  # noqa: E402


class DeduplicationTests(CliTestCase):
    def test_resaving_after_course_rename_updates_existing_item(self):
        original = self.add_items(make_item("Chain rule", "Differentiate sin(x^2)."), course="Calc")[0]
        renamed = self.run_db("rename-course", "Calc", "Calculus", "--confirmed-by-user")
        self.assertEqual(0, renamed.returncode, renamed.stderr)

        resaved = self.add_payload(
            make_payload("Calculus", "Topic", [make_item("Chain rule", "Differentiate sin(x^2).")])
        )

        self.assertEqual((0, 1), (resaved["inserted"], resaved["updated"]))
        self.assertEqual([original], resaved["ids"])
        self.assertEqual(1, self.db_json("search")["returned_count"])

    def test_old_course_name_after_rename_creates_separate_item(self):
        original = self.add_items(make_item("Chain rule", "Differentiate sin(x^2)."), course="Calc")[0]
        self.db_json("rename-course", "Calc", "Calculus", "--confirmed-by-user")

        recreated = self.add_payload(
            make_payload("Calc", "Topic", [make_item("Chain rule", "Differentiate sin(x^2).")])
        )

        self.assertEqual(1, recreated["inserted"])
        self.assertNotEqual(original, recreated["ids"][0])
        calculus = self.db_json("search", "--course", "Calculus")["items"]
        self.assertEqual([original], [item["id"] for item in calculus])


class RequeueTests(CliTestCase):
    def test_resaving_mastered_mistake_returns_it_to_review(self):
        item = make_item("Sign error", "Solve -2x > 4.")
        item_id = self.add_items(item, collection_type="mistake_set")[0]
        self.db_json("mark-done", item_id)

        resaved = self.add_payload(make_payload("Course", "Topic", [item], "mistake_set"))

        self.assertEqual([item_id], resaved["requeued"])
        found = self.db_json("search")["items"][0]
        self.assertEqual("pending", found["review_status"])
        self.assertTrue(found["due"])

    def test_resaving_question_set_item_keeps_review_state(self):
        item = make_item("Definition", "Define a limit.")
        item_id = self.add_items(item, collection_type="question_set")[0]
        self.db_json("mark-done", item_id)

        resaved = self.add_payload(make_payload("Course", "Topic", [item], "question_set"))

        self.assertEqual([], resaved["requeued"])
        self.assertEqual("done", self.db_json("search")["items"][0]["review_status"])


class EmptyCourseTests(CliTestCase):
    def course_names(self):
        return [row[0] for row in self.query_db("SELECT name FROM courses ORDER BY name")]

    def test_deleting_last_item_removes_empty_course(self):
        self.add_items(make_item("Keep", "Kept?"), course="Old")
        ghost = self.add_items(make_item("Remove", "Removed?"), course="Ghost")[0]

        self.db_json("delete", ghost, "--confirmed-by-user")
        renamed = self.run_db("rename-course", "Old", "Ghost", "--confirmed-by-user")

        self.assertEqual(0, renamed.returncode, renamed.stderr)
        self.assertEqual(["Ghost"], self.course_names())

    def test_rename_into_legacy_empty_course_succeeds(self):
        self.add_items(make_item("Keep", "Kept?"), course="Old")
        self.query_db("INSERT INTO courses(name, created_at) VALUES ('Leftover', '2026-01-01T00:00:00')")

        renamed = self.run_db("rename-course", "Old", "Leftover", "--confirmed-by-user")

        self.assertEqual(0, renamed.returncode, renamed.stderr)
        self.assertEqual(["Leftover"], self.course_names())


class SearchFilterTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.add_payload(
            make_payload(
                "Calculus",
                "Derivatives",
                [
                    make_item("Percent", "What is 50 percent of 80?"),
                    make_item("Function", "Define f(x) = x^2."),
                    make_item("Capacity", "Run the pump at 50% capacity."),
                ],
            ),
            name="derivatives.json",
        )
        self.add_payload(
            make_payload("Calculus", "Integrals", [make_item("Area", "Integrate x from 0 to 1.")]),
            name="integrals.json",
        )

    def titles(self, *args):
        return sorted(item["title"] for item in self.db_json(*args)["items"])

    def test_search_treats_like_wildcards_literally(self):
        self.assertEqual(["Capacity"], self.titles("search", "--query", "50%"))
        self.assertEqual([], self.titles("search", "--query", "f_x"))
        self.assertEqual(["Function"], self.titles("search", "--query", "f(x)"))

    def test_quiz_and_search_filter_by_topic(self):
        self.assertEqual(["Area"], self.titles("quiz", "--topic", "Integrals"))
        self.assertEqual(
            ["Capacity", "Function", "Percent"], self.titles("search", "--topic", "Derivatives")
        )

    def test_stats_can_list_topics_per_course(self):
        human = self.run_db("stats", "--human", "--topics")
        machine = self.db_json("stats", "--topics")

        self.assertEqual(0, human.returncode, human.stderr)
        self.assertIn("Derivatives (3 due / 3 pending / 3 total)", human.stdout)
        self.assertIn("Integrals (1 due / 1 pending / 1 total)", human.stdout)
        topics = machine["courses"][0]["topics"]
        self.assertEqual(["Derivatives", "Integrals"], [topic["topic"] for topic in topics])
