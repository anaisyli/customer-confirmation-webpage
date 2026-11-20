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

    def test_usage_review_can_identify_a_file_without_page_screenshots(self):
        with temporary_workspace() as root:
            data = valid_data("unused.png")
            issue = data["issues"][0]
            issue.update(
                kind="usage",
                cusId="DEMO-1-CUS-001",
                originalValues=[],
                sourcePath=None,
                sourceLocation="整份文件",
                evidence=[{
                    "caption": "培训资料.doc · 整份文件",
                    "sourceName": "培训资料.doc",
                    "location": "整份文件",
                }],
                choices=[
                    {"value": "whole", "label": "整份可使用", "detail": "请说明可用范围", "requiresCorrection": True},
                    {"value": "partial", "label": "部分可用", "detail": "请写明范围", "requiresCorrection": True},
                    {"value": "exclude", "label": "不使用", "detail": "整份排除"},
                    {"value": "pending", "label": "待核实", "detail": "继续确认", "csvAnswer": ""},
                ],
            )
            data["csv"] = {
                "headers": ["编号", "要确认的问题", "原文件名", "在文件里的位置", "回答（保留 / 不用 / 文字）"],
                "columns": ["answerId", "question", "sourceName", "sourceLocation", "answerText"],
            }
            data_path = root / "questions.json"
            data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            output = root / "review.html"
            result = subprocess.run([
                sys.executable, str(BUILDER), "--data", str(data_path), "--output", str(output)
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            html = output.read_text(encoding="utf-8")
            self.assertIn('"kind":"usage"', html)
            self.assertIn("培训资料.doc", html)
            self.assertIn("整份文件", html)
            self.assertNotIn("imagePath", html)

    def test_rejects_absolute_customer_source_path(self):
        with temporary_workspace() as root:
            (root / "page.png").write_bytes(png_1x1())
            for bad_path in (r"D:\客户资料\规格书.pdf", r"a/b.pdf； D:\客户资料\规格书.pdf", "D:spec.pdf"):
                data = valid_data("page.png")
                data["issues"][0]["sourcePath"] = bad_path
                data_path = root / "questions.json"
                data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                result = subprocess.run([
                    sys.executable, str(BUILDER), "--data", str(data_path),
                    "--output", str(root / "review.html")
                ], capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 2, bad_path)
                self.assertIn("relative to the screening package", result.stderr)

    def test_website_excerpt_linked_answers_and_correction_field(self):
        with temporary_workspace() as root:
            data = valid_data("unused.png")
            issue = data["issues"][0]
            issue.update(
                sourceName="Company Profile",
                sourceLocation="https://example.com/about",
                linkedAnswers=[{
                    "answerId": "DEMO-1-CUS-001.2",
                    "ledgerQuestion": "Confirm second page claim.",
                    "sourceName": "Company Profile EN",
                    "sourceLocation": "https://example.com/en/about",
                }],
                fields=[
                    {"key": "value", "label": "Correct value", "placeholder": "Value"},
                    {"key": "exception_detail", "label": "Exception detail", "placeholder": "Which row differs"},
                ],
                choices=[
                    {"value": "keep", "label": "Keep", "detail": "Keep as-is", "csvAnswer": "保留"},
                    {"value": "revise", "label": "Revise", "detail": "Provide correct value"},
                    {"value": "exception", "label": "Exception", "detail": "Different per row",
                     "correctionField": "exception_detail", "csvAnswer": ""},
                ],
                evidence=[{
                    "caption": "About page",
                    "sourceName": "Company Profile",
                    "sourceUrl": "https://example.com/about",
                    "location": "Hero section",
                    "capturedAt": "2026-11-20",
                    "excerpt": "Founded in 2005 with 500 employees.",
                    "highlights": ["500 employees"],
                }],
            )
            data_path = root / "questions.json"
            data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            output = root / "review.html"
            result = subprocess.run([
                sys.executable, str(BUILDER), "--data", str(data_path), "--output", str(output)
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            html = output.read_text(encoding="utf-8")
            self.assertIn("DEMO-1-CUS-001.2", html)
            self.assertIn('"correctionField":"exception_detail"', html)
            self.assertIn('"csvAnswer":"保留"', html)
            self.assertIn("https://example.com/about", html)
            self.assertIn("500 employees", html)


if __name__ == "__main__":
    unittest.main()
