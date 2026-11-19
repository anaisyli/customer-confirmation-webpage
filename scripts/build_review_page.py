#!/usr/bin/env python3
"""Embed evidence images and question data in a standalone customer review page."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import struct
import sys
from pathlib import Path


DEFAULT_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "review-template.html"
PLACEHOLDER = "__REVIEW_DATA__"
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--data", required=True, type=Path, help="Question data JSON; imagePath entries are relative to this file")
    result.add_argument("--output", required=True, type=Path, help="Final standalone HTML path")
    result.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="HTML template; defaults to this skill's asset")
    result.add_argument("--report", type=Path, help="Optional internal build report JSON")
    return result


def require_string(value: object, field: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: {field} must be a non-empty string")
    return value.strip()


def load_data(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Question data must be a JSON object")
    for field in ("projectId", "revision", "company"):
        require_string(data.get(field), field, "root")
    issues = data.get("issues")
    if not isinstance(issues, list) or not issues:
        raise ValueError("issues must be a non-empty array")
    issue_ids: set[str] = set()
    answer_ids: set[str] = set()
    for index, item in enumerate(issues, start=1):
        where = f"issues[{index - 1}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be an object")
        item["id"] = require_string(item.get("id"), "id", where)
        item["answerId"] = require_string(item.get("answerId"), "answerId", where)
        for field in ("title", "product", "question", "sourceName", "sourceLocation"):
            item[field] = require_string(item.get(field), field, where)
        if item.get("sourcePath") is not None:
            item["sourcePath"] = require_string(item.get("sourcePath"), "sourcePath", where)
        item.setdefault("ledgerQuestion", item["question"])
        item["ledgerQuestion"] = require_string(item.get("ledgerQuestion"), "ledgerQuestion", where)
        if item["id"] in issue_ids or item["answerId"] in answer_ids:
            raise ValueError(f"{where} has a duplicate id or answerId")
        issue_ids.add(item["id"])
        answer_ids.add(item["answerId"])
        values = item.get("originalValues")
        if not isinstance(values, list) or any(not isinstance(row, list) or len(row) != 2 for row in values):
            raise ValueError(f"{where}.originalValues must be an array of [label, value] pairs")
        fields = item.get("fields", [])
        if not isinstance(fields, list):
            raise ValueError(f"{where}.fields must be an array")
        field_keys: set[str] = set()
        for field in fields:
            if not isinstance(field, dict):
                raise ValueError(f"{where}.fields entries must be objects")
            key = require_string(field.get("key"), "key", where)
            label = require_string(field.get("label"), "label", where)
            placeholder = field.get("placeholder", "")
            if placeholder is None:
                placeholder = ""
            if not isinstance(placeholder, str):
                raise ValueError(f"{where}.fields placeholder must be a string")
            field["key"], field["label"], field["placeholder"] = key, label, placeholder
            if key in field_keys:
                raise ValueError(f"{where} has duplicate field key {key}")
            field_keys.add(key)
        item["fields"] = fields
        choices = item.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError(f"{where}.choices must be a non-empty array")
        choice_values: set[str] = set()
        for choice in choices:
            if not isinstance(choice, dict):
                raise ValueError(f"{where}.choices entries must be objects")
            value = require_string(choice.get("value"), "choice.value", where)
            choice["value"] = value
            choice["label"] = require_string(choice.get("label"), "choice.label", where)
            choice["detail"] = require_string(choice.get("detail"), "choice.detail", where)
            if value in choice_values:
                raise ValueError(f"{where} has duplicate choice value {value}")
            choice_values.add(value)
            if (value == "revise" or choice.get("requiresCorrection")) and not fields:
                raise ValueError(f"{where} choice {value} requires correction but no fields were defined")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"{where}.evidence must contain at least one source screenshot")
        for evidence_index, shot in enumerate(evidence, start=1):
            shot_where = f"{where}.evidence[{evidence_index - 1}]"
            if not isinstance(shot, dict):
                raise ValueError(f"{shot_where} must be an object")
            require_string(shot.get("caption"), "caption", shot_where)
            require_string(shot.get("imagePath"), "imagePath", shot_where)
    data.setdefault("createdAt", "")
    data.setdefault("title", "客户资料确认")
    data.setdefault("exportPrefix", data["projectId"])
    csv_options = data.setdefault("csv", {})
    if not isinstance(csv_options, dict):
        raise ValueError("csv must be an object")
    headers = csv_options.get("headers", ["编号", "要确认的问题", "原文件名", "在初筛包里的位置", "回答（保留 / 不用 / 文字）"])
    columns = csv_options.get("columns", ["answerId", "question", "sourceName", "sourceLocation", "answerText"])
    if not isinstance(headers, list) or not isinstance(columns, list) or len(headers) != len(columns) or not headers:
        raise ValueError("csv.headers and csv.columns must be non-empty arrays of the same length")
    if any(not isinstance(header, str) or not header.strip() for header in headers):
        raise ValueError("csv.headers must contain non-empty strings")
    if any(not isinstance(column, str) or not column.strip() for column in columns):
        raise ValueError("csv.columns must contain non-empty strings")
    csv_options["headers"], csv_options["columns"] = headers, columns
    data.setdefault("schema", "customer-review-input.v1")
    return data


def image_dimensions(raw: bytes, mime_type: str) -> tuple[int, int] | None:
    if mime_type == "image/png" and len(raw) >= 24 and raw[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", raw[16:24])
        return (w, h) if w > 0 and h > 0 else None
    if mime_type == "image/gif" and len(raw) >= 10 and raw[:6] in (b"GIF87a", b"GIF89a"):
        w, h = struct.unpack("<HH", raw[6:10])
        return (w, h) if w > 0 and h > 0 else None
    if mime_type == "image/webp" and len(raw) >= 30 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        chunk = raw[12:16]
        if chunk == b"VP8 " and len(raw) >= 30:
            w, h = struct.unpack("<HH", raw[26:30])
            w, h = w & 0x3FFF, h & 0x3FFF
            return (w, h) if w > 0 and h > 0 else None
        if chunk == b"VP8L" and len(raw) >= 25:
            bits = struct.unpack("<I", raw[21:25])[0]
            w, h = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            return (w, h) if w > 0 and h > 0 else None
        if chunk == b"VP8X" and len(raw) >= 30:
            w = int.from_bytes(raw[24:27], "little") + 1
            h = int.from_bytes(raw[27:30], "little") + 1
            return (w, h) if w > 0 and h > 0 else None
    if mime_type == "image/jpeg" and len(raw) >= 4 and raw[:2] == b"\xff\xd8":
        i = 2
        n = len(raw)
        sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while i < n:
            if raw[i] != 0xFF:
                i += 1
                continue
            while i < n and raw[i] == 0xFF:
                i += 1
            if i >= n:
                break
            marker = raw[i]
            i += 1
            if marker in (0xD8, 0xD9, 0x01) or (0xD0 <= marker <= 0xD7):
                continue
            if i + 2 > n:
                break
            seg_len = struct.unpack(">H", raw[i:i + 2])[0]
            if seg_len < 2 or i + seg_len > n:
                break
            if marker in sof_markers and seg_len >= 7:
                h, w = struct.unpack(">HH", raw[i + 3:i + 7])
                return (w, h) if w > 0 and h > 0 else None
            i += seg_len
    return None


def embed_images(data: dict, data_path: Path) -> list[dict]:
    base_dir = data_path.resolve().parent
    report: list[dict] = []
    for item in data["issues"]:
        for shot in item["evidence"]:
            path = Path(shot["imagePath"])
            if not path.is_absolute():
                path = base_dir / path
            path = path.resolve(strict=True)
            mime_type = mimetypes.guess_type(path.name)[0]
            if mime_type not in ALLOWED_IMAGE_TYPES:
                raise ValueError(f"Unsupported evidence image type: {path.name}")
            raw = path.read_bytes()
            shot["image"] = f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}"
            dims = image_dimensions(raw, mime_type)
            if dims:
                shot["width"], shot["height"] = dims
            shot.pop("imagePath", None)
            shot.setdefault("sourceName", item["sourceName"])
            report.append({
                "issueId": item["id"], "caption": shot["caption"],
                "imageName": path.name, "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
    return report


def sanitize_public_data(data: dict) -> dict:
    """Return an allowlisted copy of review data so internal notes never reach the customer HTML."""
    public: dict = {
        "schema": data["schema"],
        "projectId": data["projectId"],
        "revision": data["revision"],
        "company": data["company"],
        "createdAt": data["createdAt"],
        "title": data["title"],
        "exportPrefix": data["exportPrefix"],
        "csv": {
            "headers": list(data["csv"]["headers"]),
            "columns": list(data["csv"]["columns"]),
        },
        "issues": [],
    }
    if isinstance(data.get("instructions"), str) and data["instructions"].strip():
        public["instructions"] = data["instructions"].strip()
    for item in data["issues"]:
        entry: dict = {
            "id": item["id"],
            "answerId": item["answerId"],
            "product": item["product"],
            "title": item["title"],
            "question": item["question"],
            "ledgerQuestion": item["ledgerQuestion"],
            "originalValues": [[str(label), str(val)] for label, val in item["originalValues"]],
            "sourceName": item["sourceName"],
            "sourceLocation": item["sourceLocation"],
            "fields": [
                {"key": f["key"], "label": f["label"], "placeholder": f.get("placeholder", "")}
                for f in item["fields"]
            ],
            "choices": [
                {
                    "value": c["value"],
                    "label": c["label"],
                    "detail": c["detail"],
                    **({"requiresCorrection": True} if c.get("requiresCorrection") else {}),
                }
                for c in item["choices"]
            ],
            "evidence": [
                {
                    "caption": s["caption"],
                    "image": s["image"],
                    "sourceName": s.get("sourceName", item["sourceName"]),
                    **({"width": s["width"], "height": s["height"]} if "width" in s and "height" in s else {}),
                }
                for s in item["evidence"]
            ],
        }
        if item.get("sourcePath"):
            entry["sourcePath"] = item["sourcePath"]
        public["issues"].append(entry)
    return public


def main() -> int:
    args = parser().parse_args()
    try:
        data_path = args.data.resolve(strict=True)
        template_path = args.template.resolve(strict=True)
        data = load_data(data_path)
        evidence = embed_images(data, data_path)
        public_data = sanitize_public_data(data)
        template = template_path.read_text(encoding="utf-8")
        if template.count(PLACEHOLDER) != 1:
            raise ValueError(f"Template must contain {PLACEHOLDER!r} exactly once")
        encoded = json.dumps(public_data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        html = template.replace(PLACEHOLDER, encoded)
        output.write_text(html, encoding="utf-8", newline="\n")
        if args.report:
            report_path = args.report.resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps({
                "projectId": data["projectId"], "revision": data["revision"],
                "issueCount": len(data["issues"]), "evidenceCount": len(evidence),
                "output": str(output), "outputBytes": output.stat().st_size,
                "outputSha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "evidence": evidence,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(output), "issues": len(data["issues"]),
                          "evidence": len(evidence), "bytes": output.stat().st_size}, ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
