import json
import shutil
import struct
import subprocess
import sys
import unittest
import zlib
from contextlib import contextmanager
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
BUILDER = SKILL / "scripts" / "build_review_page.py"


@contextmanager
def temporary_workspace():
    root = Path(__file__).with_name("_tmp_test_run")
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def png_1x1() -> bytes:
    def chunk(name: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00\x20\x80\xe0\xff")) + chunk(b"IEND", b"")


def valid_data(image_path: str) -> dict:
    return {
        "projectId": "DEMO-1", "revision": "r1", "company": "Demo Co", "createdAt": "2026-11-19",
        "issues": [{
            "id": "DEMO-1-CUS-001", "answerId": "DEMO-1-CUS-001.1", "title": "Which value is correct?",
            "product": "Model X", "question": "Please choose the correct source value.",
            "originalValues": [["Source A", "10 mm"], ["Source B", "12 mm"]],
            "sourceName": "spec.pdf", "sourceLocation": "Page 2, dimensions table",
            "fields": [{"key": "value", "label": "Correct value", "placeholder": "Value and unit"}],
            "choices": [
                {"value": "a", "label": "Use A", "detail": "10 mm"},
                {"value": "revise", "label": "Revise", "detail": "Enter the correct value", "requiresCorrection": True}
            ],
            "evidence": [{"caption": "spec.pdf · Page 2", "imagePath": image_path}]
        }]
    }


class BuildReviewPageTests(unittest.TestCase):
    def test_embeds_image_and_build_report(self):
        with temporary_workspace() as root:
            (root / "evidence").mkdir()
            (root / "evidence" / "page.png").write_bytes(png_1x1())
            data_path = root / "questions.json"
            data_path.write_text(json.dumps(valid_data("evidence/page.png")), encoding="utf-8")
            output = root / "customer" / "review.html"
            report = root / "internal" / "report.json"
            result = subprocess.run([
                sys.executable, str(BUILDER), "--data", str(data_path),
                "--output", str(output), "--report", str(report)
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            html = output.read_text(encoding="utf-8")
            self.assertIn("data:image/png;base64,", html)
            self.assertIn("customer-feedback.v1", html)
            self.assertNotIn('"imagePath"', html)
            self.assertIn("DEMO-1-CUS-001.1", html)
            self.assertIn('"width":1,"height":1', html)
            built = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(built["issueCount"], 1)
            self.assertEqual(built["evidenceCount"], 1)

    def test_strips_internal_fields_and_preserves_source_path(self):
        with temporary_workspace() as root:
            (root / "page.png").write_bytes(png_1x1())
            data = valid_data("page.png")
            data["internalSecret"] = "DO_NOT_LEAK_ROOT"
            data["issues"][0]["priorReviewRequired"] = "DO_NOT_LEAK_ISSUE"
            data["issues"][0]["sourcePath"] = "01_规格书/Model-X/spec.pdf"
            del data["issues"][0]["fields"][0]["placeholder"]
            data_path = root / "questions.json"
            data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            output = root / "review.html"
            result = subprocess.run([
                sys.executable, str(BUILDER), "--data", str(data_path),
                "--output", str(output)
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            html = output.read_text(encoding="utf-8")
            self.assertNotIn("DO_NOT_LEAK_ROOT", html)
            self.assertNotIn("DO_NOT_LEAK_ISSUE", html)
            self.assertNotIn("priorReviewRequired", html)
            self.assertIn("01_规格书/Model-X/spec.pdf", html)
            self.assertIn('"placeholder":""', html)

    def test_rejects_revise_choice_without_fields(self):
        with temporary_workspace() as root:
            (root / "page.png").write_bytes(png_1x1())
            data = valid_data("page.png")
            data["issues"][0]["fields"] = []
            data["issues"][0]["choices"] = [
                {"value": "a", "label": "Use A", "detail": "10 mm"},
                {"value": "revise", "label": "Revise", "detail": "Enter value"}
            ]
            data_path = root / "questions.json"
            data_path.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(BUILDER), "--data", str(data_path),
                "--output", str(root / "review.html")
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("requires correction", result.stderr)

    def test_rejects_duplicate_answer_ids(self):
        with temporary_workspace() as root:
            (root / "page.png").write_bytes(png_1x1())
            data = valid_data("page.png")
            second = json.loads(json.dumps(data["issues"][0]))
            second["id"] = "DEMO-1-CUS-002"
            data["issues"].append(second)
            data_path = root / "questions.json"
            data_path.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(BUILDER), "--data", str(data_path),
                "--output", str(root / "review.html")
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("duplicate", result.stderr)
            self.assertFalse((root / "review.html").exists())


if __name__ == "__main__":
    unittest.main()
