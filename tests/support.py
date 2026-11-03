import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PYTHON = Path(sys.executable)
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_review_set.py"
SKILL = ROOT / "SKILL.md"
README = ROOT / "README.md"
README_ZH_CN = ROOT / "README.zh-CN.md"
SCHEMA_REFERENCE = ROOT / "references" / "schema.md"
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


def write_image(path, image_format="PNG", color=(20, 40, 60)):
    from PIL import Image

    Image.new("RGB", (4, 4), color=color).save(path, format=image_format)
    return path


def load_script_module():
    spec = importlib.util.spec_from_file_location("export_review_set_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def subcommands(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


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


def make_item(title, question, **overrides):
    item = {
        "title": title,
        "original_question": question,
        "knowledge_points": ["Knowledge point"],
        "mistake_reason": "Needs review",
        "correct_approach": "Follow the saved approach.",
        "answer_points": [f"Answer for {title}"],
        "review_suggestion": "Redo one similar problem.",
    }
    item.update(overrides)
    return item


def make_payload(course, topic, items, collection_type="question_set"):
    return {
        "course": course,
        "collection_type": collection_type,
        "topic": topic,
        "items": items,
    }


class CliTestCase(unittest.TestCase):
    """Runs the CLI against an isolated, symlink-resolved temporary notes root."""

    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name).resolve()
        self.db_path = self.root / "exercise_bank.sqlite3"

    def run_cli(self, *args):
        return subprocess.run(
            [str(PYTHON), str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def run_db(self, *args):
        return self.run_cli("db", *args, "--db-path", str(self.db_path))

    def db_json(self, *args):
        result = self.run_db(*args)
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def write_json(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def add_payload(self, payload, name="input.json"):
        return self.db_json(
            "add", "--input", str(self.write_json(name, payload)), "--confirmed-selection-by-user"
        )

    def add_items(self, *items, course="Course", topic="Topic", collection_type="question_set"):
        return self.add_payload(make_payload(course, topic, list(items), collection_type))["ids"]

    def query_db(self, sql, params=()):
        import sqlite3

        connection = sqlite3.connect(self.db_path)
        try:
            rows = connection.execute(sql, params).fetchall()
            connection.commit()
            return rows
        finally:
            connection.close()
