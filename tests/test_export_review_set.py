import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


PYTHON = Path(r"C:\Users\Zippe\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_review_set.py"
SKILL = ROOT / "SKILL.md"
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


def write_image(path, image_format="PNG"):
    Image.new("RGB", (4, 4), color=(20, 40, 60)).save(path, format=image_format)
    return path


def load_script_module():
    spec = importlib.util.spec_from_file_location("export_review_set_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_directory_link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if created.returncode != 0:
            raise OSError(created.stderr or created.stdout)
    else:
        os.symlink(target, link, target_is_directory=True)


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


class AttachmentFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "exercise_bank.sqlite3"

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [str(PYTHON), str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def make_input(self, *, attachments=None):
        input_path = self.root / "attachment-input.json"
        item = {
            "title": "Visual prompt",
            "original_question": "Use the network figure to answer.",
            "knowledge_points": ["Network representation"],
            "mistake_reason": "Needs review",
            "correct_approach": "Read the prompt diagram.",
            "answer_points": ["Use the link data."],
            "review_suggestion": "Retry with the diagram.",
        }
        if attachments is not None:
            item["attachments"] = attachments
        input_path.write_text(
            json.dumps(
                {
                    "course": "Images",
                    "collection_type": "question_set",
                    "topic": "Attachment foundation",
                    "items": [item],
                }
            ),
            encoding="utf-8",
        )
        return input_path

    def test_text_only_add_creates_attachment_table_without_changing_item_shape(self):
        added = self.run_cli(
            "db",
            "add",
            "--input",
            str(self.make_input()),
            "--confirmed-selection-by-user",
            "--db-path",
            str(self.db_path),
        )
        self.assertEqual(0, added.returncode, added.stderr)
        found = self.run_cli("db", "search", "--db-path", str(self.db_path))
        self.assertEqual(0, found.returncode, found.stderr)
        self.assertNotIn("attachments", json.loads(found.stdout)["items"][0])
        connection = sqlite3.connect(self.db_path)
        try:
            table_names = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        finally:
            connection.close()
        self.assertIn("question_attachments", table_names)

    def test_validate_rejects_non_image_renamed_png_without_writing_files(self):
        bad_image = self.root / "fake.png"
        bad_image.write_text("not an image", encoding="utf-8")
        input_path = self.make_input(
            attachments=[
                {
                    "source_path": str(bad_image),
                    "role": "prompt",
                    "provenance": "provided",
                    "caption": "Fake diagram",
                }
            ]
        )

        validated = self.run_cli("db", "validate", "--input", str(input_path))

        self.assertNotEqual(0, validated.returncode)
        self.assertIn("valid image", validated.stderr)
        self.assertFalse((self.root / "attachments").exists())


class AttachmentImportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "exercise_bank.sqlite3"
        self.image_path = write_image(self.root / "network.png")

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [str(PYTHON), str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def make_input(self, *, attachment=True, role="prompt", source_path=None):
        item = {
            "title": "Link layout",
            "original_question": "Which link is tolled?",
            "knowledge_points": ["Network representation"],
            "mistake_reason": "Needs review",
            "correct_approach": "Read the network figure.",
            "answer_points": ["Link 2 is tolled."],
            "review_suggestion": "Re-read the prompt figure.",
        }
        if attachment:
            item["attachments"] = [
                {
                    "source_path": str(source_path or self.image_path),
                    "role": role,
                    "provenance": "provided",
                    "caption": "Prompt network",
                }
            ]
        input_path = self.root / f"input-{role}-{attachment}.json"
        input_path.write_text(
            json.dumps(
                {
                    "course": "Images",
                    "collection_type": "question_set",
                    "topic": "Managed image",
                    "items": [item],
                }
            ),
            encoding="utf-8",
        )
        return input_path

    def add_input(self, input_path):
        return self.run_cli(
            "db",
            "add",
            "--input",
            str(input_path),
            "--confirmed-selection-by-user",
            "--db-path",
            str(self.db_path),
        )

    def find_item(self):
        searched = self.run_cli("db", "search", "--db-path", str(self.db_path))
        self.assertEqual(0, searched.returncode, searched.stderr)
        return json.loads(searched.stdout)["items"][0]

    def test_add_with_prompt_image_copies_file_and_returns_metadata(self):
        added = self.add_input(self.make_input())

        self.assertEqual(0, added.returncode, added.stderr)
        added_attachment = json.loads(added.stdout)["items"][0]["attachments"][0]
        self.assertEqual("prompt", added_attachment["role"])
        attachment = self.find_item()["attachments"][0]
        managed_path = Path(attachment["managed_path"])
        self.assertEqual("prompt", attachment["role"])
        self.assertEqual("image/png", attachment["media_type"])
        self.assertTrue(managed_path.exists())
        self.assertEqual(self.root / "attachments", managed_path.parents[1])

    def test_jpeg_input_is_stored_with_canonical_jpg_extension(self):
        jpeg_path = write_image(self.root / "figure.untrusted", "JPEG")

        added = self.add_input(self.make_input(source_path=jpeg_path))

        self.assertEqual(0, added.returncode, added.stderr)
        attachment = self.find_item()["attachments"][0]
        self.assertEqual(".jpg", Path(attachment["managed_path"]).suffix)

    def test_webp_input_is_stored_with_canonical_webp_extension(self):
        webp_path = write_image(self.root / "figure.untrusted", "WEBP")

        added = self.add_input(self.make_input(source_path=webp_path))

        self.assertEqual(0, added.returncode, added.stderr)
        attachment = self.find_item()["attachments"][0]
        self.assertEqual(".webp", Path(attachment["managed_path"]).suffix)

    def test_add_duplicate_image_keeps_one_managed_copy(self):
        first = self.add_input(self.make_input())
        second = self.add_input(self.make_input())

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(1, len(self.find_item()["attachments"]))
        self.assertEqual(1, len(list((self.root / "attachments").rglob("*.png"))))

    def test_duplicate_image_with_different_role_requires_metadata_update(self):
        first = self.add_input(self.make_input(role="prompt"))
        second = self.add_input(self.make_input(role="solution"))

        self.assertEqual(0, first.returncode, first.stderr)
        self.assertNotEqual(0, second.returncode)
        self.assertIn("attachment-update", second.stderr)

    def test_add_rejects_modified_orphan_at_expected_destination(self):
        added = self.add_input(self.make_input(attachment=False))
        self.assertEqual(0, added.returncode, added.stderr)
        item_id = json.loads(added.stdout)["ids"][0]
        digest = hashlib.sha256(self.image_path.read_bytes()).hexdigest()
        destination = self.root / "attachments" / item_id / f"network-{digest}.png"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"unexpected orphan content")

        attempted = self.add_input(self.make_input())

        self.assertNotEqual(0, attempted.returncode)
        self.assertIn("differs from verified source digest", attempted.stderr)
        self.assertNotIn("attachments", self.find_item())

    def test_attach_backfills_an_existing_text_only_question(self):
        added = self.add_input(self.make_input(attachment=False))
        self.assertEqual(0, added.returncode, added.stderr)
        item_id = json.loads(added.stdout)["ids"][0]

        attached = self.run_cli(
            "db",
            "attach",
            item_id,
            "--source",
            str(self.image_path),
            "--role",
            "prompt",
            "--provenance",
            "provided",
            "--caption",
            "Prompt network",
            "--db-path",
            str(self.db_path),
        )

        self.assertEqual(0, attached.returncode, attached.stderr)
        self.assertEqual("prompt", self.find_item()["attachments"][0]["role"])

    def test_second_attachment_failure_rolls_back_first_created_file(self):
        added = self.add_input(self.make_input(attachment=False))
        self.assertEqual(0, added.returncode, added.stderr)
        item_id = json.loads(added.stdout)["ids"][0]
        second_image = self.root / "second.png"
        Image.new("RGB", (4, 4), color=(80, 10, 120)).save(second_image, format="PNG")
        digest = hashlib.sha256(second_image.read_bytes()).hexdigest()
        conflict = self.root / "attachments" / item_id / f"second-{digest}.png"
        conflict.parent.mkdir(parents=True)
        conflict.write_bytes(b"conflict")
        input_path = self.root / "two-attachments.json"
        payload = json.loads(self.make_input().read_text(encoding="utf-8"))
        payload["items"][0]["attachments"].append(
            {
                "source_path": str(second_image),
                "role": "prompt",
                "provenance": "provided",
                "caption": "Second prompt",
            }
        )
        input_path.write_text(json.dumps(payload), encoding="utf-8")

        attempted = self.add_input(input_path)

        self.assertNotEqual(0, attempted.returncode)
        self.assertNotIn("attachments", self.find_item())
        first_copies = list((self.root / "attachments" / item_id).glob("network-*.png"))
        self.assertEqual([], first_copies)

    def test_concurrent_attach_keeps_one_valid_managed_copy(self):
        added = self.add_input(self.make_input(attachment=False))
        self.assertEqual(0, added.returncode, added.stderr)
        item_id = json.loads(added.stdout)["ids"][0]
        command = [
            str(PYTHON),
            str(SCRIPT),
            "db",
            "attach",
            item_id,
            "--source",
            str(self.image_path),
            "--role",
            "prompt",
            "--provenance",
            "provided",
            "--caption",
            "Prompt network",
            "--db-path",
            str(self.db_path),
        ]

        first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        first_stdout, first_stderr = first.communicate()
        second_stdout, second_stderr = second.communicate()

        self.assertEqual(0, first.returncode, first_stderr)
        self.assertEqual(0, second.returncode, second_stderr)
        item = self.find_item()
        self.assertEqual(1, len(item["attachments"]))
        managed = Path(item["attachments"][0]["managed_path"])
        with Image.open(managed) as copied:
            copied.verify()
        self.assertEqual([], list(managed.parent.glob("*.tmp")))

    def test_write_validates_the_copied_snapshot_not_a_stale_source_check(self):
        added = self.add_input(self.make_input(attachment=False))
        self.assertEqual(0, added.returncode, added.stderr)
        item_id = json.loads(added.stdout)["ids"][0]
        module = load_script_module()
        original_open = module.Image.open
        source = self.image_path.resolve()

        class ReplacedAfterVerify:
            def __init__(self, image):
                self.image = image

            def __enter__(self):
                self.image.__enter__()
                return self

            def __exit__(self, *args):
                return self.image.__exit__(*args)

            @property
            def format(self):
                return self.image.format

            def verify(self):
                self.image.verify()
                source.write_bytes(b"not an image after source validation")

        def swap_source_after_validation(path, *args, **kwargs):
            image = original_open(path, *args, **kwargs)
            if Path(path).resolve() == source:
                return ReplacedAfterVerify(image)
            return image

        context = module.StorageContext(self.db_path, self.root, self.root / "attachments")
        created_files = []
        connection = module.connect_db(self.db_path)
        try:
            module.Image.open = swap_source_after_validation
            try:
                row = module.add_attachment_row(
                    connection,
                    context,
                    item_id,
                    {
                        "source_path": str(source),
                        "role": "prompt",
                        "provenance": "provided",
                        "caption": "Prompt network",
                    },
                    created_files,
                )
                connection.commit()
            finally:
                module.Image.open = original_open
        finally:
            connection.close()
        managed_path = Path(module.serialize_attachment(row, context)["managed_path"])
        with Image.open(managed_path) as copied:
            copied.verify()


class AttachmentPathSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "exercise_bank.sqlite3"
        self.image_path = write_image(self.root / "diagram.png")
        self.input_path = self.root / "question.json"
        self.input_path.write_text(
            json.dumps(
                {
                    "course": "Path Safety",
                    "collection_type": "question_set",
                    "topic": "Attachments",
                    "items": [
                        {
                            "title": "Safe attachment path",
                            "original_question": "Inspect the diagram.",
                            "knowledge_points": ["Path safety"],
                            "mistake_reason": "Needs review",
                            "correct_approach": "Keep files managed.",
                            "answer_points": ["Do not follow links."],
                            "review_suggestion": "Audit attachments.",
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
            str(self.input_path),
            "--confirmed-selection-by-user",
            "--db-path",
            str(self.db_path),
        )
        self.assertEqual(0, added.returncode, added.stderr)
        self.item_id = json.loads(added.stdout)["ids"][0]

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [str(PYTHON), str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def attach(self):
        return self.run_cli(
            "db",
            "attach",
            self.item_id,
            "--source",
            str(self.image_path),
            "--role",
            "prompt",
            "--provenance",
            "provided",
            "--caption",
            "prompt-diagram",
            "--db-path",
            str(self.db_path),
        )

    def test_attach_rejects_junction_managed_root_without_writing_outside(self):
        outside = self.root / "outside"
        outside.mkdir()
        make_directory_link(self.root / "attachments", outside)

        attached = self.attach()

        self.assertNotEqual(0, attached.returncode)
        self.assertIn("Unsafe managed attachment path", attached.stderr)
        self.assertEqual([], list(outside.rglob("*")))

    def test_snapshot_rejects_parent_directory_question_component(self):
        module = load_script_module()
        context = module.StorageContext(self.db_path, self.root, self.root / "attachments")

        with self.assertRaisesRegex(ValueError, "Unsafe managed attachment path"):
            module.create_verified_snapshot(
                context,
                "..",
                {
                    "source_path": str(self.image_path),
                    "role": "prompt",
                    "provenance": "provided",
                    "caption": "prompt-diagram",
                },
            )

        self.assertEqual([], list(self.root.glob(".incoming-*.tmp")))

    def test_unsafe_linked_question_directory_is_reported_and_detachable_without_file_delete(self):
        attached = self.attach()
        self.assertEqual(0, attached.returncode, attached.stderr)
        attachment = json.loads(attached.stdout)["attachment"]
        original_dir = Path(attachment["managed_path"]).parent
        outside = self.root / "outside-question"
        original_dir.rename(outside)
        make_directory_link(original_dir, outside)

        exported = self.run_cli(
            "db", "export", "--course", "Path Safety", "--mode", "questions-only", "--db-path", str(self.db_path)
        )
        audited = self.run_cli("db", "attachment-audit", "--db-path", str(self.db_path))
        detached = self.run_cli(
            "db",
            "detach",
            str(attachment["id"]),
            "--confirmed-by-user",
            "--db-path",
            str(self.db_path),
        )

        self.assertNotEqual(0, exported.returncode)
        self.assertIn("Unsafe managed attachment path", exported.stderr)
        self.assertEqual(0, audited.returncode, audited.stderr)
        self.assertTrue(json.loads(audited.stdout)["unsafe_paths"])
        self.assertEqual(0, detached.returncode, detached.stderr)
        self.assertTrue(json.loads(detached.stdout)["file_cleanup_skipped"])
        self.assertTrue((outside / Path(attachment["managed_path"]).name).exists())

    def test_unsafe_linked_question_directory_allows_metadata_delete_without_file_delete(self):
        attached = self.attach()
        self.assertEqual(0, attached.returncode, attached.stderr)
        attachment = json.loads(attached.stdout)["attachment"]
        original_dir = Path(attachment["managed_path"]).parent
        outside = self.root / "outside-delete"
        original_dir.rename(outside)
        make_directory_link(original_dir, outside)

        deleted = self.run_cli(
            "db",
            "delete",
            self.item_id,
            "--confirmed-by-user",
            "--db-path",
            str(self.db_path),
        )

        self.assertEqual(0, deleted.returncode, deleted.stderr)
        self.assertTrue(json.loads(deleted.stdout)["file_cleanup_skipped"])
        self.assertTrue((outside / Path(attachment["managed_path"]).name).exists())


class AttachmentVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "exercise_bank.sqlite3"
        self.prompt_path = write_image(self.root / "prompt.png")
        self.solution_path = self.root / "solution.png"
        Image.new("RGB", (4, 4), color=(100, 120, 140)).save(self.solution_path, format="PNG")
        input_path = self.root / "visible-images.json"
        input_path.write_text(
            json.dumps(
                {
                    "course": "Images",
                    "collection_type": "mistake_set",
                    "topic": "Visibility",
                    "items": [
                        {
                            "title": "Prompt and solution",
                            "original_question": "Interpret the prompt network.",
                            "knowledge_points": ["Visibility"],
                            "mistake_reason": "Read the wrong link.",
                            "correct_approach": "Read the prompt before solving.",
                            "answer_points": ["The solution uses Link 2."],
                            "review_suggestion": "Hide solution material until answered.",
                            "attachments": [
                                {
                                    "source_path": str(self.prompt_path),
                                    "role": "prompt",
                                    "provenance": "provided",
                                    "caption": "prompt-network",
                                },
                                {
                                    "source_path": str(self.solution_path),
                                    "role": "solution",
                                    "provenance": "provided",
                                    "caption": "solution-working",
                                },
                            ],
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

    def export_content(self, mode):
        exported = self.run_cli(
            "db",
            "export",
            "--course",
            "Images",
            "--mode",
            mode,
            "--db-path",
            str(self.db_path),
        )
        self.assertEqual(0, exported.returncode, exported.stderr)
        return Path(json.loads(exported.stdout)["markdown_path"]).read_text(encoding="utf-8")

    def test_quiz_and_due_return_prompt_attachment_without_solution_attachment(self):
        for command_name in ("quiz", "due"):
            result = self.run_cli(
                "db", command_name, "--course", "Images", "--db-path", str(self.db_path)
            )
            self.assertEqual(0, result.returncode, result.stderr)
            attachments = json.loads(result.stdout)["items"][0]["attachments"]
            self.assertEqual(["prompt"], [attachment["role"] for attachment in attachments])

    def test_questions_only_export_includes_prompt_without_solution_image(self):
        content = self.export_content("questions-only")

        self.assertIn("prompt-network", content)
        self.assertNotIn("solution-working", content)

    def test_full_export_places_solution_image_after_answer_heading(self):
        content = self.export_content("full")

        self.assertLess(content.index("prompt-network"), content.index("### 回答"))
        self.assertGreater(content.index("solution-working"), content.index("### 回答"))

    def test_search_content_renders_both_attachment_roles(self):
        searched = self.run_cli(
            "db", "search", "--course", "Images", "--include-content", "--db-path", str(self.db_path)
        )

        self.assertEqual(0, searched.returncode, searched.stderr)
        content = json.loads(searched.stdout)["items"][0]["content"]
        self.assertIn("prompt-network", content)
        self.assertIn("solution-working", content)

    def test_export_rejects_tampered_managed_image(self):
        searched = json.loads(
            self.run_cli("db", "search", "--course", "Images", "--db-path", str(self.db_path)).stdout
        )
        solution = next(
            attachment
            for attachment in searched["items"][0]["attachments"]
            if attachment["role"] == "solution"
        )
        Path(solution["managed_path"]).write_bytes(b"modified")

        exported = self.run_cli(
            "db", "export", "--course", "Images", "--mode", "full", "--db-path", str(self.db_path)
        )

        self.assertNotEqual(0, exported.returncode)
        self.assertIn("differs from stored", exported.stderr)

    def test_export_encodes_attachment_paths_and_normalizes_multiline_caption(self):
        special_path = self.root / "route #图(1).png"
        Image.new("RGB", (4, 4), color=(40, 90, 150)).save(special_path, format="PNG")
        item = json.loads(
            self.run_cli("db", "search", "--course", "Images", "--db-path", str(self.db_path)).stdout
        )["items"][0]
        attached = self.run_cli(
            "db",
            "attach",
            item["id"],
            "--source",
            str(special_path),
            "--role",
            "prompt",
            "--provenance",
            "provided",
            "--caption",
            "network\n[detail]",
            "--db-path",
            str(self.db_path),
        )
        self.assertEqual(0, attached.returncode, attached.stderr)

        content = self.export_content("questions-only")

        self.assertIn("![network \\[detail\\]]", content)
        self.assertIn("route%20%23%E5%9B%BE%281%29-", content)
        self.assertNotIn("network\n", content)

    def test_export_rejects_replaced_oversized_attachment_before_hashing(self):
        searched = json.loads(
            self.run_cli("db", "search", "--course", "Images", "--db-path", str(self.db_path)).stdout
        )
        prompt = next(
            attachment
            for attachment in searched["items"][0]["attachments"]
            if attachment["role"] == "prompt"
        )
        with Path(prompt["managed_path"]).open("wb") as handle:
            handle.truncate(MAX_ATTACHMENT_BYTES + 1)

        exported = self.run_cli(
            "db", "export", "--course", "Images", "--mode", "questions-only", "--db-path", str(self.db_path)
        )
        audit = self.run_cli("db", "attachment-audit", "--db-path", str(self.db_path))

        self.assertNotEqual(0, exported.returncode)
        self.assertIn("size differs", exported.stderr)
        self.assertEqual(0, audit.returncode, audit.stderr)
        self.assertEqual(1, len(json.loads(audit.stdout)["modified_files"]))


class AttachmentMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "exercise_bank.sqlite3"
        prompt_path = write_image(self.root / "prompt.png")
        solution_path = self.root / "solution.png"
        Image.new("RGB", (4, 4), color=(150, 80, 10)).save(solution_path, format="PNG")
        input_path = self.root / "maintenance.json"
        input_path.write_text(
            json.dumps(
                {
                    "course": "Maintenance",
                    "collection_type": "mistake_set",
                    "topic": "Attachment maintenance",
                    "items": [
                        {
                            "title": "Maintain attachments",
                            "original_question": "Read the drawing.",
                            "knowledge_points": ["Maintenance"],
                            "mistake_reason": "Needs review",
                            "correct_approach": "Review managed images.",
                            "answer_points": ["Keep safe files."],
                            "review_suggestion": "Audit later.",
                            "attachments": [
                                {
                                    "source_path": str(prompt_path),
                                    "role": "prompt",
                                    "provenance": "provided",
                                    "caption": "maintenance-prompt",
                                },
                                {
                                    "source_path": str(solution_path),
                                    "role": "solution",
                                    "provenance": "provided",
                                    "caption": "maintenance-solution",
                                },
                            ],
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
        self.item_id = json.loads(added.stdout)["ids"][0]
        item = self.search_item()
        self.prompt = next(a for a in item["attachments"] if a["role"] == "prompt")
        self.solution = next(a for a in item["attachments"] if a["role"] == "solution")
        self.question_dir = Path(self.prompt["managed_path"]).parent

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [str(PYTHON), str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def search_item(self):
        searched = self.run_cli("db", "search", "--db-path", str(self.db_path))
        self.assertEqual(0, searched.returncode, searched.stderr)
        return json.loads(searched.stdout)["items"][0]

    def test_attachment_update_requires_confirmation_before_role_change(self):
        rejected = self.run_cli(
            "db",
            "attachment-update",
            str(self.solution["id"]),
            "--role",
            "prompt",
            "--db-path",
            str(self.db_path),
        )

        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("explicit user confirmation", rejected.stderr)
        changed = self.run_cli(
            "db",
            "attachment-update",
            str(self.solution["id"]),
            "--role",
            "prompt",
            "--confirmed-by-user",
            "--db-path",
            str(self.db_path),
        )
        self.assertEqual(0, changed.returncode, changed.stderr)
        roles = [a["role"] for a in self.search_item()["attachments"]]
        self.assertEqual(["prompt", "prompt"], roles)

    def test_detach_requires_confirmation_and_preserves_unmanaged_file(self):
        unmanaged = self.question_dir / "keep-me.txt"
        unmanaged.write_text("do not delete", encoding="utf-8")
        rejected = self.run_cli(
            "db", "detach", str(self.prompt["id"]), "--db-path", str(self.db_path)
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("explicit user confirmation", rejected.stderr)

        detached = self.run_cli(
            "db",
            "detach",
            str(self.prompt["id"]),
            "--confirmed-by-user",
            "--db-path",
            str(self.db_path),
        )

        self.assertEqual(0, detached.returncode, detached.stderr)
        self.assertFalse(Path(self.prompt["managed_path"]).exists())
        self.assertTrue(unmanaged.exists())
        self.assertEqual(1, len(self.search_item()["attachments"]))

    def test_delete_requires_confirmation_before_removing_managed_files(self):
        rejected = self.run_cli("db", "delete", self.item_id, "--db-path", str(self.db_path))

        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("explicit user confirmation", rejected.stderr)
        self.assertTrue(Path(self.prompt["managed_path"]).exists())

    def test_confirmed_delete_removes_referenced_images_and_preserves_unmanaged_file(self):
        unmanaged = self.question_dir / "keep-me.txt"
        unmanaged.write_text("not managed", encoding="utf-8")

        deleted = self.run_cli(
            "db",
            "delete",
            self.item_id,
            "--confirmed-by-user",
            "--db-path",
            str(self.db_path),
        )

        self.assertEqual(0, deleted.returncode, deleted.stderr)
        self.assertFalse(Path(self.prompt["managed_path"]).exists())
        self.assertFalse(Path(self.solution["managed_path"]).exists())
        self.assertTrue(unmanaged.exists())

    def test_audit_reports_missing_modified_and_orphan_files_without_deleting(self):
        Path(self.prompt["managed_path"]).unlink()
        Path(self.solution["managed_path"]).write_bytes(b"altered")
        orphan = self.question_dir / "orphan.png"
        write_image(orphan)

        audit = self.run_cli("db", "attachment-audit", "--db-path", str(self.db_path))

        self.assertEqual(0, audit.returncode, audit.stderr)
        report = json.loads(audit.stdout)
        self.assertEqual(1, len(report["missing_files"]))
        self.assertEqual(1, len(report["modified_files"]))
        self.assertEqual([str(orphan)], report["orphan_files"])
        self.assertTrue(orphan.exists())

    def test_audit_cleanup_requires_confirmation_and_can_prune_orphans(self):
        orphan = self.question_dir / "orphan.png"
        write_image(orphan)

        rejected = self.run_cli(
            "db", "attachment-audit", "--prune-orphans", "--db-path", str(self.db_path)
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("explicit user confirmation", rejected.stderr)
        self.assertTrue(orphan.exists())

        cleaned = self.run_cli(
            "db",
            "attachment-audit",
            "--prune-orphans",
            "--confirmed-by-user",
            "--db-path",
            str(self.db_path),
        )
        self.assertEqual(0, cleaned.returncode, cleaned.stderr)
        self.assertFalse(orphan.exists())


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


class AttachmentInstructionsTests(unittest.TestCase):
    def test_skill_documents_safe_optional_image_workflow(self):
        instructions = SKILL.read_text(encoding="utf-8")

        self.assertIn("Attachment Decision Rule", instructions)
        self.assertIn("`prompt`", instructions)
        self.assertIn("`solution`", instructions)
        self.assertIn(
            "Solution images must not appear in `db quiz`, `db due`, or `questions-only` exports",
            instructions,
        )
        self.assertIn("db attachment-update <attachment_id>", instructions)
        self.assertIn("db detach <attachment_id> --confirmed-by-user", instructions)
        self.assertIn("db delete <item_id> --confirmed-by-user", instructions)
        self.assertIn("unsafe_paths", instructions)
        self.assertIn("file_cleanup_skipped", instructions)

    def test_schema_documents_attachment_import_fields(self):
        schema = (ROOT / "references" / "schema.md").read_text(encoding="utf-8")

        for token in ("attachments", "source_path", "role", "provenance", "caption"):
            self.assertIn(token, schema)


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

        deleted = self.run_cli(
            "db",
            "delete",
            self.item_id,
            "--confirmed-by-user",
            "--db-path",
            str(self.db_path),
        )

        self.assertEqual(0, deleted.returncode, deleted.stderr)
        self.assertEqual(self.item_id, json.loads(deleted.stdout)["deleted_item"]["id"])
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0])
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM review_events").fetchone()[0])
        finally:
            connection.close()

    def test_delete_missing_question_fails(self):
        deleted = self.run_cli(
            "db",
            "delete",
            "missing-item",
            "--confirmed-by-user",
            "--db-path",
            str(self.db_path),
        )

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
