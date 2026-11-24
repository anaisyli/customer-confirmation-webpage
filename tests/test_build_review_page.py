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

    def test_internal_mode_builds_grouped_page_with_member_images_and_audit_drawers(self):
        with temporary_workspace() as root:
            (root / "evidence").mkdir()
            (root / "evidence" / "drawing1.png").write_bytes(png_1x1())
            (root / "evidence" / "table1.png").write_bytes(png_1x1())
            data = {
                "schema": "internal-review-input.v1",
                "mode": "internal",
                "projectId": "P-010",
                "revision": "20261124-1",
                "company": "Smart Display Co",
                "createdAt": "2026-11-24",
                "autoResolved": [{
                    "id": "AUTO-001",
                    "ledger": "TERM",
                    "title": "420 个普通电子产品术语按词典自动放行",
                    "ruleVersion": "vocab-display-v2",
                    "evidenceCount": 420,
                    "reason": "精确命中版本化行业词典，排除认证与品牌词。",
                    "members": [{"label": "luminance", "detail": "命中 38 处"}, "backlit"],
                }],
                "riskRetained": [{
                    "id": "P-010-ANM-001",
                    "ledger": "ANM",
                    "title": "18 页 AI/Reducto 识别页已完成原图核对（保留 ⚠）",
                    "evidenceCount": 18,
                    "reason": "已对照原图核对无内容错误，退出待办，保留来源标记。",
                    "riskNote": "整理页引用时仍需保留 ⚠ 提示。",
                    "members": ["产品画册-A · 第 3 页"],
                }],
                "cusSummary": {
                    "draftGroups": 1,
                    "sentGroups": 2,
                    "items": [
                        {"id": "P-010-CUS-024", "title": "扭矩表 DN65/PN10 原印 292 待核实", "status": "待汇总", "kind": "事实参数", "evidenceCount": 1},
                        {"id": "P-010-CUS-006", "title": "首批 6 份案例与图片授权", "status": "已发待答", "batchId": "B01", "kind": "授权许可", "evidenceCount": 6},
                    ],
                },
                "issues": [{
                    "id": "P-010-MAT-007-G1",
                    "rootKey": "dimension_drawing_model:neighbor_title",
                    "ledger": "MAT",
                    "category": "尺寸图型号",
                    "product": "DS50 系列",
                    "title": "12 张尺寸图可按相邻规格标题归属型号",
                    "question": "以下尺寸图虽未在图内印型号，但相邻页标题均为 DS50，请确认是否整组归属（可取消个别例外）。",
                    "recommendedAction": "confirm_model",
                    "recommendedReason": "相邻规格页标题与文件目录完全一致，仅第 8 页为通用结构图需留意。",
                    "impactSummary": "关闭 MAT-007 中 12 张尺寸图待确认记录",
                    "ruleVersion": "drawing-group-v1",
                    "owner": "知识库专员",
                    "internalNote": "此组不问客户；排除的成员保持型号待确认。",
                    "sourcePath": r"D:\Vault\P-010\02_源资料\10_净化原文\规格书-DS50.md",
                    "vaultLinks": [{"label": "规格书-DS50", "path": "02_源资料/10_净化原文/规格书/规格书-DS50.md"}],
                    "originalValues": [["候选型号", "DS50"], ["分组依据", "相邻页标题匹配"]],
                    "members": [
                        {"memberId": "item-1|page 3", "label": "DS50 正视图（第 3 页）", "location": "page 3", "imagePath": "evidence/drawing1.png", "selected": True},
                        {"memberId": "item-1|page 8", "label": "通用安装结构图（第 8 页）", "location": "page 8", "selected": False},
                    ],
                    "evidence": [{"caption": "相邻参数表 · 第 2 页", "imagePath": "evidence/table1.png"}],
                    "fields": [{"key": "cus_question", "label": "转客户问法", "placeholder": "写清希望客户确认的内容"}],
                    "choices": [
                        {"value": "confirm_model", "label": "按推荐型号批量确认", "detail": "勾选成员归属 DS50 并关闭", "targetStatus": "closed"},
                        {"value": "keep_unconfirmed", "label": "保留为型号待确认", "detail": "不问客户，继续保留在待确认组", "targetStatus": "risk_retained"},
                        {"value": "escalate_cus", "label": "转客户确认", "detail": "生成待汇总 CUS", "targetStatus": "escalated_cus", "correctionField": "cus_question"},
                    ],
                }],
            }
            data_path = root / "internal-review.json"
            data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            output = root / "内部决策" / "P-010-内部待办决策页.html"
            report = root / "内部交接" / "internal-report.json"
            result = subprocess.run([
                sys.executable, str(BUILDER), "--mode", "internal",
                "--data", str(data_path), "--output", str(output), "--report", str(report),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            html = output.read_text(encoding="utf-8")
            self.assertIn("内部审核专用 · 严禁发给客户", html)
            self.assertIn("governance-decisions.v1", html)
            self.assertNotIn("customer-feedback.v1", html)
            self.assertNotIn('"imagePath"', html)
            self.assertIn("data:image/png;base64,", html)
            self.assertIn("dimension_drawing_model:neighbor_title", html)
            self.assertIn("此组不问客户", html)
            self.assertIn("vocab-display-v2", html)
            built = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(built["mode"], "internal")
            self.assertEqual(built["groupCount"], 1)
            self.assertEqual(built["memberCount"], 2)
            self.assertEqual(built["autoResolvedCount"], 1)
            self.assertEqual(built["riskRetainedCount"], 1)
            self.assertEqual(built["evidenceCount"], 2)
            self.assertEqual(built["summary"]["actionableGroups"], 1)
            self.assertEqual(built["summary"]["totalEvidenceCount"], 440)

    def test_internal_mode_blocks_customer_delivery_folder(self):
        with temporary_workspace() as root:
            data = {
                "schema": "internal-review-input.v1",
                "projectId": "P-010",
                "revision": "r1",
                "autoResolved": [{"id": "AUTO-1", "title": "普通词已放行", "evidenceCount": 10}],
                "issues": [],
            }
            data_path = root / "internal-review.json"
            data_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            for forbidden_dir in ("交付客户", "customer-delivery", "to-customer"):
                out = root / forbidden_dir / "review.html"
                result = subprocess.run([
                    sys.executable, str(BUILDER), "--mode", "internal",
                    "--data", str(data_path), "--output", str(out),
                ], capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 2, forbidden_dir)
                self.assertIn("customer delivery folder", result.stderr)
                self.assertFalse(out.exists())

    def test_mode_mismatch_is_rejected_in_both_directions(self):
        with temporary_workspace() as root:
            (root / "page.png").write_bytes(png_1x1())
            customer_path = root / "questions.json"
            customer_path.write_text(json.dumps(valid_data("page.png")), encoding="utf-8")
            res1 = subprocess.run([
                sys.executable, str(BUILDER), "--mode", "internal",
                "--data", str(customer_path), "--output", str(root / "内部决策" / "out.html"),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(res1.returncode, 2)
            self.assertIn("customer review data cannot be built in internal mode", res1.stderr.lower())

            internal_path = root / "internal.json"
            internal_path.write_text(json.dumps({
                "schema": "internal-review-input.v1",
                "mode": "internal",
                "projectId": "P-010",
                "revision": "r1",
                "company": "Demo Co",
                "autoResolved": [{"id": "AUTO-1", "title": "自动放行"}],
                "issues": [],
            }, ensure_ascii=False), encoding="utf-8")
            res2 = subprocess.run([
                sys.executable, str(BUILDER),
                "--data", str(internal_path), "--output", str(root / "交付客户" / "out.html"),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(res2.returncode, 2)
            self.assertIn("Internal review data cannot be built in customer mode", res2.stderr)

    def test_internal_mode_validation_and_audit_only_zero_actionable(self):
        with temporary_workspace() as root:
            audit_only = root / "audit-only.json"
            audit_only.write_text(json.dumps({
                "schema": "internal-review-input.v1",
                "projectId": "P-002",
                "revision": "r1",
                "riskRetained": [{"id": "P-002-ANM-002", "ledger": "ANM", "title": "22 页识别来源提示保留 ⚠", "evidenceCount": 22}],
                "issues": [],
            }, ensure_ascii=False), encoding="utf-8")
            out = root / "内部决策" / "audit-only.html"
            ok = subprocess.run([
                sys.executable, str(BUILDER), "--mode", "internal",
                "--data", str(audit_only), "--output", str(out),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("P-002-ANM-002", out.read_text(encoding="utf-8"))

            bad_rec = root / "bad-rec.json"
            bad_rec.write_text(json.dumps({
                "schema": "internal-review-input.v1",
                "projectId": "P-002",
                "revision": "r1",
                "issues": [{
                    "id": "G1",
                    "title": "Test",
                    "question": "Decide",
                    "recommendedAction": "nonexistent",
                    "originalValues": [["A", "1"]],
                    "choices": [{"value": "keep", "label": "Keep", "detail": "Keep it"}],
                }],
            }), encoding="utf-8")
            fail = subprocess.run([
                sys.executable, str(BUILDER), "--mode", "internal",
                "--data", str(bad_rec), "--output", str(root / "内部决策" / "bad.html"),
            ], capture_output=True, text=True, check=False)
            self.assertEqual(fail.returncode, 2)
            self.assertIn("recommendedAction", fail.stderr)


if __name__ == "__main__":
    unittest.main()
