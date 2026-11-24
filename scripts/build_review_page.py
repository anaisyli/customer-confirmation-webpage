#!/usr/bin/env python3
"""Embed evidence images and question data in a standalone customer review page."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import re
import struct
import sys
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "review-template.html"
INTERNAL_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "internal-review-template.html"
PLACEHOLDER = "__REVIEW_DATA__"
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
ALLOWED_MODES = ("customer", "internal")
ALLOWED_INTERNAL_LEDGERS = {"MAT", "ANM", "CUS", "TERM", "D", "OTHER"}
FORBIDDEN_INTERNAL_OUTPUT_DIRS = {"交付客户", "customer-delivery", "to-customer", "deliver-to-customer"}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--mode", choices=ALLOWED_MODES, default="customer",
                        help="Page mode: customer (default, strict public output) or internal (MAT/ANM/CUS internal triage)")
    result.add_argument("--data", required=True, type=Path, help="Question/decision data JSON; imagePath entries are relative to this file")
    result.add_argument("--output", required=True, type=Path, help="Final standalone HTML path")
    result.add_argument("--template", type=Path, help="HTML template; defaults to the template for --mode")
    result.add_argument("--report", type=Path, help="Optional internal build report JSON")
    return result


def require_string(value: object, field: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: {field} must be a non-empty string")
    return value.strip()


def optional_string(value: object, field: str, where: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{where}: {field} must be a string")
    return value.strip()


def optional_non_negative_int(value: object, field: str, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{where}: {field} must be a non-negative integer")
    return value


def load_data(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Question data must be a JSON object")
    if data.get("mode") == "internal" or data.get("schema") == "internal-review-input.v1":
        raise ValueError("Internal review data cannot be built in customer mode; pass --mode internal")
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
            for raw_path in item["sourcePath"].split("；"):
                source_path = raw_path.strip()
                if (not source_path
                        or re.match(r"^(?:[A-Za-z]:|[/\\]{1,2})", source_path)
                        or any(part == ".." for part in re.split(r"[/\\]+", source_path))):
                    raise ValueError(f"{where}.sourcePath must stay relative to the screening package")
        if "cusId" in item:
            item["cusId"] = require_string(item["cusId"], "cusId", where)
        kind = item.get("kind", "fact")
        if kind not in ("fact", "usage", "replacement"):
            raise ValueError(f"{where}.kind must be fact, usage, or replacement")
        item["kind"] = kind
        item.setdefault("ledgerQuestion", item["question"])
        item["ledgerQuestion"] = require_string(item.get("ledgerQuestion"), "ledgerQuestion", where)
        if item["id"] in issue_ids or item["answerId"] in answer_ids:
            raise ValueError(f"{where} has a duplicate id or answerId")
        issue_ids.add(item["id"])
        answer_ids.add(item["answerId"])
        linked_answers = item.get("linkedAnswers", [])
        if not isinstance(linked_answers, list):
            raise ValueError(f"{where}.linkedAnswers must be an array")
        for linked in linked_answers:
            if not isinstance(linked, dict):
                raise ValueError(f"{where}.linkedAnswers entries must be objects")
            for field in ("answerId", "ledgerQuestion", "sourceName", "sourceLocation"):
                linked[field] = require_string(linked.get(field), field, where)
            if linked["answerId"] in answer_ids:
                raise ValueError(f"{where} has a duplicate linked answerId")
            answer_ids.add(linked["answerId"])
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
            if "csvAnswer" in choice and choice["csvAnswer"] not in ("保留", "不用", ""):
                raise ValueError(f"{where}.choices csvAnswer must be 保留, 不用, or empty")
            if (value == "revise" or choice.get("requiresCorrection")) and not fields:
                raise ValueError(f"{where} choice {value} requires correction but no fields were defined")
            if "correctionField" in choice:
                key = require_string(choice["correctionField"], "choice.correctionField", where)
                if key not in field_keys:
                    raise ValueError(f"{where} choice {value} uses unknown correction field {key}")
                choice["correctionField"] = key
        dedicated_fields = {choice["correctionField"] for choice in choices if "correctionField" in choice}
        if dedicated_fields and field_keys <= dedicated_fields and any(
                "correctionField" not in choice and (choice["value"] == "revise" or choice.get("requiresCorrection"))
                for choice in choices):
            raise ValueError(f"{where} requires a correction field available to general choices")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"{where}.evidence must contain at least one identified source")
        for evidence_index, shot in enumerate(evidence):
            shot_where = f"{where}.evidence[{evidence_index}]"
            if not isinstance(shot, dict):
                raise ValueError(f"{shot_where} must be an object")
            require_string(shot.get("caption"), "caption", shot_where)
            if "highlighted" in shot and not isinstance(shot["highlighted"], bool):
                raise ValueError(f"{shot_where}.highlighted must be a boolean")
            source_url = shot.get("sourceUrl")
            if source_url is not None:
                source_url = require_string(source_url, "sourceUrl", shot_where)
                parsed = urlsplit(source_url)
                if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError(f"{shot_where}: sourceUrl must be a public HTTP(S) URL")
                shot["sourceUrl"] = source_url
                for name in ("sourceName", "location", "capturedAt"):
                    shot[name] = require_string(shot.get(name), name, shot_where)
            if "excerpt" in shot:
                if source_url is None:
                    for name in ("sourceName", "location"):
                        shot[name] = require_string(shot.get(name), name, shot_where)
                excerpt = require_string(shot["excerpt"], "excerpt", shot_where)
                shot["excerpt"] = excerpt
                highlights = shot.get("highlights", [])
                if not isinstance(highlights, list) or any(
                    not isinstance(fragment, str) or not fragment.strip() or fragment not in excerpt
                    for fragment in highlights
                ):
                    raise ValueError(f"{shot_where}: highlights must be verbatim fragments of the excerpt")
            if "imagePath" in shot:
                require_string(shot.get("imagePath"), "imagePath", shot_where)
            elif "excerpt" not in shot:
                if kind != "usage":
                    raise ValueError(f"{shot_where}: evidence needs an image or excerpt")
                for name in ("sourceName", "location"):
                    shot[name] = require_string(shot.get(name), name, shot_where)
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
            if "imagePath" not in shot:
                report.append({
                    "issueId": item["id"], "caption": shot["caption"],
                    **{key: shot[key] for key in ("sourceName", "sourceUrl", "location", "capturedAt") if key in shot},
                    **({"sha256": hashlib.sha256(shot["excerpt"].encode("utf-8")).hexdigest()}
                       if "excerpt" in shot else {"identificationOnly": True}),
                })
                continue
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
                **{key: shot[key] for key in ("sourceUrl", "location", "capturedAt") if key in shot},
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
            "kind": item["kind"],
            "answerId": item["answerId"],
            **({"linkedAnswers": [{key: linked[key] for key in
                                 ("answerId", "ledgerQuestion", "sourceName", "sourceLocation")}
                                 for linked in item["linkedAnswers"]]} if item.get("linkedAnswers") else {}),
            **({"cusId": item["cusId"]} if "cusId" in item else {}),
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
                    **({"correctionField": c["correctionField"]} if "correctionField" in c else {}),
                    **({"csvAnswer": c["csvAnswer"]} if "csvAnswer" in c else {}),
                }
                for c in item["choices"]
            ],
            "evidence": [
                {
                    "caption": s["caption"],
                    **{key: s[key] for key in ("image", "sourceUrl", "location", "capturedAt", "excerpt", "highlights", "highlighted")
                       if key in s},
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


def validate_internal_output_path(output: Path) -> None:
    """Prevent internal review pages from ever landing inside a customer delivery folder."""
    forbidden = {name.casefold() for name in FORBIDDEN_INTERNAL_OUTPUT_DIRS}
    for part in output.resolve().parent.parts:
        if part.strip().casefold() in forbidden:
            raise ValueError(
                f"Internal review page must not be written into a customer delivery folder ({part!r}); "
                "use an internal folder such as 内部决策/ or 内部交接/"
            )


def _validate_pairs(raw: object, field: str, where: str) -> list[list[str]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(row, list) or len(row) != 2 for row in raw):
        raise ValueError(f"{where}.{field} must be an array of [label, value] pairs")
    return [[str(label), str(val)] for label, val in raw]


def _validate_vault_links(raw: object, where: str) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{where}.vaultLinks must be an array")
    links: list[dict[str, str]] = []
    for idx, entry in enumerate(raw):
        entry_where = f"{where}.vaultLinks[{idx}]"
        if isinstance(entry, str) and entry.strip():
            links.append({"label": entry.strip(), "path": entry.strip()})
        elif isinstance(entry, dict):
            path_val = require_string(entry.get("path"), "path", entry_where)
            label_val = optional_string(entry.get("label"), "label", entry_where, default=path_val)
            links.append({"label": label_val or path_val, "path": path_val})
        else:
            raise ValueError(f"{entry_where} must be a non-empty string or object with path")
    return links


def _validate_audit_records(raw: object, section: str) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"root.{section} must be an array")
    records: list[dict] = []
    seen_ids: set[str] = set()
    for idx, entry in enumerate(raw):
        where = f"{section}[{idx}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} must be an object")
        rec_id = require_string(entry.get("id"), "id", where)
        if rec_id in seen_ids:
            raise ValueError(f"{where} has duplicate id {rec_id}")
        seen_ids.add(rec_id)
        title = require_string(entry.get("title"), "title", where)
        reason = optional_string(entry.get("reason"), "reason", where)
        ledger = optional_string(entry.get("ledger"), "ledger", where)
        if ledger and ledger not in ALLOWED_INTERNAL_LEDGERS:
            raise ValueError(f"{where}.ledger must be one of {sorted(ALLOWED_INTERNAL_LEDGERS)}")
        raw_members = entry.get("members", [])
        if not isinstance(raw_members, list):
            raise ValueError(f"{where}.members must be an array")
        members: list[dict[str, str]] = []
        for m_idx, member in enumerate(raw_members):
            m_where = f"{where}.members[{m_idx}]"
            if isinstance(member, str) and member.strip():
                members.append({"label": member.strip(), "detail": ""})
            elif isinstance(member, dict):
                members.append({
                    "label": require_string(member.get("label"), "label", m_where),
                    "detail": optional_string(member.get("detail"), "detail", m_where),
                })
            else:
                raise ValueError(f"{m_where} must be a non-empty string or object with label")
        ev_count = optional_non_negative_int(entry.get("evidenceCount"), "evidenceCount", where)
        if ev_count is None:
            ev_count = len(members) if members else 1
        record: dict = {
            "id": rec_id,
            "title": title,
            "reason": reason,
            "evidenceCount": ev_count,
            "members": members,
        }
        if ledger:
            record["ledger"] = ledger
        for opt_key in ("category", "ruleVersion", "riskNote"):
            val = optional_string(entry.get(opt_key), opt_key, where)
            if val:
                record[opt_key] = val
        vault_links = _validate_vault_links(entry.get("vaultLinks"), where)
        if vault_links:
            record["vaultLinks"] = vault_links
        records.append(record)
    return records


def load_internal_data(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Internal review data must be a JSON object")
    if data.get("mode") == "customer" or data.get("schema") == "customer-review-input.v1" or "csv" in data:
        raise ValueError("Customer review data cannot be built in internal mode; omit --mode or pass --mode customer")
    if data.get("mode") != "internal" and data.get("schema") != "internal-review-input.v1":
        raise ValueError(
            "Internal mode requires schema 'internal-review-input.v1' or mode 'internal' in the input JSON "
            "(customer review data cannot be built in internal mode)"
        )
    for field in ("projectId", "revision"):
        data[field] = require_string(data.get(field), field, "root")
    data["company"] = optional_string(data.get("company"), "company", "root")
    data["createdAt"] = optional_string(data.get("createdAt"), "createdAt", "root")
    data["title"] = optional_string(data.get("title"), "title", "root", default="内部待办决策页") or "内部待办决策页"
    data["exportPrefix"] = optional_string(data.get("exportPrefix"), "exportPrefix", "root", default=data["projectId"]) or data["projectId"]
    data["instructions"] = optional_string(data.get("instructions"), "instructions", "root")
    data["schema"] = optional_string(data.get("schema"), "schema", "root", default="internal-review-input.v1") or "internal-review-input.v1"
    data["mode"] = "internal"

    auto_resolved = _validate_audit_records(data.get("autoResolved"), "autoResolved")
    risk_retained = _validate_audit_records(data.get("riskRetained"), "riskRetained")
    data["autoResolved"] = auto_resolved
    data["riskRetained"] = risk_retained

    raw_cus = data.get("cusSummary")
    cus_summary: dict = {"draftGroups": 0, "sentGroups": 0, "totalGroups": 0, "items": []}
    if raw_cus is not None:
        if not isinstance(raw_cus, dict):
            raise ValueError("root.cusSummary must be an object")
        raw_items = raw_cus.get("items", [])
        if not isinstance(raw_items, list):
            raise ValueError("root.cusSummary.items must be an array")
        items_out: list[dict] = []
        for idx, entry in enumerate(raw_items):
            where = f"cusSummary.items[{idx}]"
            if not isinstance(entry, dict):
                raise ValueError(f"{where} must be an object")
            item_out: dict = {
                "id": require_string(entry.get("id"), "id", where),
                "title": require_string(entry.get("title"), "title", where),
                "status": optional_string(entry.get("status"), "status", where, default="待汇总"),
                "batchId": optional_string(entry.get("batchId"), "batchId", where),
                "kind": optional_string(entry.get("kind"), "kind", where),
                "evidenceCount": optional_non_negative_int(entry.get("evidenceCount"), "evidenceCount", where) or 1,
            }
            items_out.append(item_out)
        draft_groups = optional_non_negative_int(raw_cus.get("draftGroups"), "draftGroups", "cusSummary")
        sent_groups = optional_non_negative_int(raw_cus.get("sentGroups"), "sentGroups", "cusSummary")
        total_groups = optional_non_negative_int(raw_cus.get("totalGroups"), "totalGroups", "cusSummary")
        if draft_groups is None:
            draft_groups = sum(1 for entry in items_out if entry["status"] in {"待汇总", "draft"})
        if sent_groups is None:
            sent_groups = sum(1 for entry in items_out if entry["status"] in {"已发待答", "sent"})
        if total_groups is None:
            total_groups = len(items_out) if items_out else (draft_groups + sent_groups)
        cus_summary = {
            "draftGroups": draft_groups,
            "sentGroups": sent_groups,
            "totalGroups": total_groups,
            "items": items_out,
        }
    data["cusSummary"] = cus_summary

    issues = data.get("issues", [])
    if not isinstance(issues, list):
        raise ValueError("issues must be an array")
    if not issues and not auto_resolved and not risk_retained:
        raise ValueError("Internal review data must contain at least one actionable issue or audit record")

    issue_ids: set[str] = set()
    for index, item in enumerate(issues):
        where = f"issues[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be an object")
        item["id"] = require_string(item.get("id"), "id", where)
        if item["id"] in issue_ids:
            raise ValueError(f"{where} has duplicate id {item['id']}")
        issue_ids.add(item["id"])
        item["rootKey"] = optional_string(item.get("rootKey"), "rootKey", where, default=item["id"]) or item["id"]
        ledger = optional_string(item.get("ledger"), "ledger", where, default="ANM") or "ANM"
        if ledger not in ALLOWED_INTERNAL_LEDGERS:
            raise ValueError(f"{where}.ledger must be one of {sorted(ALLOWED_INTERNAL_LEDGERS)}")
        item["ledger"] = ledger
        item["category"] = optional_string(item.get("category"), "category", where)
        item["product"] = optional_string(item.get("product"), "product", where) or optional_string(item.get("scope"), "scope", where) or item["category"] or ledger
        item["title"] = require_string(item.get("title"), "title", where)
        item["question"] = require_string(item.get("question"), "question", where)
        item["recommendedReason"] = optional_string(item.get("recommendedReason"), "recommendedReason", where)
        item["impactSummary"] = optional_string(item.get("impactSummary"), "impactSummary", where)
        item["ruleVersion"] = optional_string(item.get("ruleVersion"), "ruleVersion", where)
        item["owner"] = optional_string(item.get("owner"), "owner", where)
        item["sourceName"] = optional_string(item.get("sourceName"), "sourceName", where)
        item["sourceLocation"] = optional_string(item.get("sourceLocation"), "sourceLocation", where)
        item["sourcePath"] = optional_string(item.get("sourcePath"), "sourcePath", where)
        item["internalNote"] = optional_string(item.get("internalNote"), "internalNote", where)
        if "cusId" in item and item["cusId"] is not None:
            item["cusId"] = require_string(item["cusId"], "cusId", where)
        else:
            item.pop("cusId", None)
        if "answerId" in item and item["answerId"] is not None:
            item["answerId"] = require_string(item["answerId"], "answerId", where)
        else:
            item.pop("answerId", None)
        item["originalValues"] = _validate_pairs(item.get("originalValues"), "originalValues", where)
        item["vaultLinks"] = _validate_vault_links(item.get("vaultLinks"), where)

        fields = item.get("fields", [])
        if not isinstance(fields, list):
            raise ValueError(f"{where}.fields must be an array")
        field_keys: set[str] = set()
        for field in fields:
            if not isinstance(field, dict):
                raise ValueError(f"{where}.fields entries must be objects")
            key = require_string(field.get("key"), "key", where)
            label = require_string(field.get("label"), "label", where)
            placeholder = optional_string(field.get("placeholder"), "placeholder", where)
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
            if "targetStatus" in choice and choice["targetStatus"] is not None:
                choice["targetStatus"] = require_string(choice["targetStatus"], "choice.targetStatus", where)
            if (value == "revise" or choice.get("requiresCorrection")) and not fields:
                raise ValueError(f"{where} choice {value} requires correction but no fields were defined")
            if "correctionField" in choice and choice["correctionField"] is not None:
                key = require_string(choice["correctionField"], "choice.correctionField", where)
                if key not in field_keys:
                    raise ValueError(f"{where} choice {value} uses unknown correction field {key}")
                choice["correctionField"] = key
        dedicated_fields = {choice["correctionField"] for choice in choices if "correctionField" in choice}
        if dedicated_fields and field_keys <= dedicated_fields and any(
                "correctionField" not in choice and (choice["value"] == "revise" or choice.get("requiresCorrection"))
                for choice in choices):
            raise ValueError(f"{where} requires a correction field available to general choices")

        rec = optional_string(item.get("recommendedAction"), "recommendedAction", where)
        if rec and rec not in choice_values:
            raise ValueError(f"{where}.recommendedAction {rec!r} does not match any choice value")
        item["recommendedAction"] = rec

        raw_members = item.get("members", [])
        if not isinstance(raw_members, list):
            raise ValueError(f"{where}.members must be an array")
        member_ids: set[str] = set()
        members_out: list[dict] = []
        for m_idx, member in enumerate(raw_members):
            m_where = f"{where}.members[{m_idx}]"
            if not isinstance(member, dict):
                raise ValueError(f"{m_where} must be an object")
            m_id = require_string(member.get("memberId"), "memberId", m_where)
            if m_id in member_ids:
                raise ValueError(f"{m_where} has duplicate memberId {m_id}")
            member_ids.add(m_id)
            selected = member.get("selected", True)
            if not isinstance(selected, bool):
                raise ValueError(f"{m_where}.selected must be a boolean")
            member_out: dict = {
                "memberId": m_id,
                "label": require_string(member.get("label"), "label", m_where),
                "detail": optional_string(member.get("detail"), "detail", m_where),
                "sourceName": optional_string(member.get("sourceName"), "sourceName", m_where),
                "location": optional_string(member.get("location"), "location", m_where),
                "selected": selected,
            }
            if "imagePath" in member and member["imagePath"] is not None:
                member_out["imagePath"] = require_string(member["imagePath"], "imagePath", m_where)
            meta_pairs = _validate_pairs(member.get("meta"), "meta", m_where)
            if meta_pairs:
                member_out["meta"] = meta_pairs
            members_out.append(member_out)
        item["members"] = members_out

        evidence = item.get("evidence", [])
        if not isinstance(evidence, list):
            raise ValueError(f"{where}.evidence must be an array")
        for ev_idx, shot in enumerate(evidence):
            shot_where = f"{where}.evidence[{ev_idx}]"
            if not isinstance(shot, dict):
                raise ValueError(f"{shot_where} must be an object")
            shot["caption"] = require_string(shot.get("caption"), "caption", shot_where)
            if "highlighted" in shot and not isinstance(shot["highlighted"], bool):
                raise ValueError(f"{shot_where}.highlighted must be a boolean")
            source_url = shot.get("sourceUrl")
            if source_url is not None:
                source_url = require_string(source_url, "sourceUrl", shot_where)
                parsed = urlsplit(source_url)
                if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError(f"{shot_where}: sourceUrl must be a public HTTP(S) URL")
                shot["sourceUrl"] = source_url
            if "excerpt" in shot and shot["excerpt"] is not None:
                excerpt = require_string(shot["excerpt"], "excerpt", shot_where)
                shot["excerpt"] = excerpt
                highlights = shot.get("highlights", [])
                if not isinstance(highlights, list) or any(
                    not isinstance(fragment, str) or not fragment.strip() or fragment not in excerpt
                    for fragment in highlights
                ):
                    raise ValueError(f"{shot_where}: highlights must be verbatim fragments of the excerpt")
                shot["highlights"] = highlights
            if "imagePath" in shot and shot["imagePath"] is not None:
                shot["imagePath"] = require_string(shot["imagePath"], "imagePath", shot_where)
            shot["sourceName"] = optional_string(shot.get("sourceName"), "sourceName", shot_where, default=item["sourceName"])
            shot["location"] = optional_string(shot.get("location"), "location", shot_where)
            shot["capturedAt"] = optional_string(shot.get("capturedAt"), "capturedAt", shot_where)
        item["evidence"] = evidence

        if not evidence and not members_out and not item["originalValues"]:
            raise ValueError(f"{where}: internal issue needs evidence, members, or originalValues")

        ev_count = optional_non_negative_int(item.get("evidenceCount"), "evidenceCount", where)
        if ev_count is None:
            ev_count = len(members_out) if members_out else max(len(evidence), 1)
        item["evidenceCount"] = ev_count

    raw_summary = data.get("summary")
    if raw_summary is not None and not isinstance(raw_summary, dict):
        raise ValueError("root.summary must be an object")
    raw_summary = raw_summary or {}
    actionable_groups = optional_non_negative_int(raw_summary.get("actionableGroups"), "actionableGroups", "summary")
    auto_groups = optional_non_negative_int(raw_summary.get("autoResolvedGroups"), "autoResolvedGroups", "summary")
    risk_groups = optional_non_negative_int(raw_summary.get("riskRetainedGroups"), "riskRetainedGroups", "summary")
    cus_groups = optional_non_negative_int(raw_summary.get("cusPendingGroups"), "cusPendingGroups", "summary")
    total_evidence = optional_non_negative_int(raw_summary.get("totalEvidenceCount"), "totalEvidenceCount", "summary")
    computed_evidence = (
        sum(item["evidenceCount"] for item in issues)
        + sum(rec["evidenceCount"] for rec in auto_resolved)
        + sum(rec["evidenceCount"] for rec in risk_retained)
    )
    data["summary"] = {
        "actionableGroups": len(issues) if actionable_groups is None else actionable_groups,
        "autoResolvedGroups": len(auto_resolved) if auto_groups is None else auto_groups,
        "riskRetainedGroups": len(risk_retained) if risk_groups is None else risk_groups,
        "cusPendingGroups": cus_summary["totalGroups"] if cus_groups is None else cus_groups,
        "totalEvidenceCount": computed_evidence if total_evidence is None else total_evidence,
    }
    return data


def _embed_single_image(holder: dict, base_dir: Path) -> dict:
    path = Path(holder["imagePath"])
    if not path.is_absolute():
        path = base_dir / path
    path = path.resolve(strict=True)
    mime_type = mimetypes.guess_type(path.name)[0]
    if mime_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError(f"Unsupported evidence image type: {path.name}")
    raw = path.read_bytes()
    holder["image"] = f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}"
    dims = image_dimensions(raw, mime_type)
    if dims:
        holder["width"], holder["height"] = dims
    holder.pop("imagePath", None)
    return {"imageName": path.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def embed_internal_images(data: dict, data_path: Path) -> list[dict]:
    base_dir = data_path.resolve().parent
    report: list[dict] = []
    for item in data["issues"]:
        for shot in item["evidence"]:
            if "imagePath" in shot:
                meta = _embed_single_image(shot, base_dir)
                report.append({
                    "issueId": item["id"], "kind": "evidence", "caption": shot["caption"],
                    **meta,
                    **{key: shot[key] for key in ("sourceUrl", "location", "capturedAt") if shot.get(key)},
                })
            else:
                report.append({
                    "issueId": item["id"], "kind": "evidence", "caption": shot["caption"],
                    **{key: shot[key] for key in ("sourceName", "sourceUrl", "location", "capturedAt") if shot.get(key)},
                    **({"sha256": hashlib.sha256(shot["excerpt"].encode("utf-8")).hexdigest()}
                       if shot.get("excerpt") else {"identificationOnly": True}),
                })
        for member in item["members"]:
            if "imagePath" in member:
                meta = _embed_single_image(member, base_dir)
                report.append({
                    "issueId": item["id"], "kind": "member", "memberId": member["memberId"],
                    "caption": member["label"], **meta,
                })
    return report


def sanitize_internal_data(data: dict) -> dict:
    """Return a clean allowlisted copy of internal review data."""
    payload: dict = {
        "schema": data["schema"],
        "mode": "internal",
        "projectId": data["projectId"],
        "revision": data["revision"],
        "company": data["company"],
        "createdAt": data["createdAt"],
        "title": data["title"],
        "exportPrefix": data["exportPrefix"],
        "summary": dict(data["summary"]),
        "autoResolved": data["autoResolved"],
        "riskRetained": data["riskRetained"],
        "cusSummary": data["cusSummary"],
        "issues": [],
    }
    if data.get("instructions"):
        payload["instructions"] = data["instructions"]
    for item in data["issues"]:
        entry: dict = {
            "id": item["id"],
            "rootKey": item["rootKey"],
            "ledger": item["ledger"],
            "category": item["category"],
            "product": item["product"],
            "title": item["title"],
            "question": item["question"],
            "evidenceCount": item["evidenceCount"],
            "originalValues": item["originalValues"],
            "vaultLinks": item["vaultLinks"],
            "fields": item["fields"],
            "choices": [
                {
                    "value": c["value"],
                    "label": c["label"],
                    "detail": c["detail"],
                    **({"targetStatus": c["targetStatus"]} if c.get("targetStatus") else {}),
                    **({"requiresCorrection": True} if c.get("requiresCorrection") else {}),
                    **({"correctionField": c["correctionField"]} if "correctionField" in c else {}),
                }
                for c in item["choices"]
            ],
            "members": [
                {
                    "memberId": m["memberId"],
                    "label": m["label"],
                    **({"detail": m["detail"]} if m.get("detail") else {}),
                    **({"sourceName": m["sourceName"]} if m.get("sourceName") else {}),
                    **({"location": m["location"]} if m.get("location") else {}),
                    "selected": m["selected"],
                    **({"meta": m["meta"]} if m.get("meta") else {}),
                    **({"image": m["image"]} if "image" in m else {}),
                    **({"width": m["width"], "height": m["height"]} if "width" in m and "height" in m else {}),
                }
                for m in item["members"]
            ],
            "evidence": [
                {
                    "caption": s["caption"],
                    **{key: s[key] for key in ("image", "sourceUrl", "location", "capturedAt", "excerpt", "highlights", "highlighted", "sourceName")
                       if key in s and s[key] not in ("", None)},
                    **({"width": s["width"], "height": s["height"]} if "width" in s and "height" in s else {}),
                }
                for s in item["evidence"]
            ],
        }
        for opt_key in ("recommendedAction", "recommendedReason", "impactSummary", "ruleVersion",
                        "owner", "sourceName", "sourceLocation", "sourcePath", "internalNote", "cusId", "answerId"):
            if item.get(opt_key):
                entry[opt_key] = item[opt_key]
        payload["issues"].append(entry)
    return payload


def main() -> int:
    args = parser().parse_args()
    try:
        mode = args.mode
        data_path = args.data.resolve(strict=True)
        default_template = INTERNAL_TEMPLATE if mode == "internal" else DEFAULT_TEMPLATE
        template_path = (args.template or default_template).resolve(strict=True)
        output = args.output.resolve()
        if mode == "internal":
            validate_internal_output_path(output)
            data = load_internal_data(data_path)
            evidence = embed_internal_images(data, data_path)
            rendered_data = sanitize_internal_data(data)
        else:
            data = load_data(data_path)
            evidence = embed_images(data, data_path)
            rendered_data = sanitize_public_data(data)
        template = template_path.read_text(encoding="utf-8")
        if template.count(PLACEHOLDER) != 1:
            raise ValueError(f"Template must contain {PLACEHOLDER!r} exactly once")
        encoded = json.dumps(rendered_data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")
        output.parent.mkdir(parents=True, exist_ok=True)
        html = template.replace(PLACEHOLDER, encoded)
        output.write_text(html, encoding="utf-8", newline="\n")
        if args.report:
            report_path = args.report.resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_payload: dict = {
                "mode": mode,
                "projectId": data["projectId"], "revision": data["revision"],
                "issueCount": len(data["issues"]), "evidenceCount": len(evidence),
                "output": str(output), "outputBytes": output.stat().st_size,
                "outputSha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "evidence": evidence,
            }
            if mode == "internal":
                report_payload.update({
                    "groupCount": len(data["issues"]),
                    "memberCount": sum(len(item["members"]) for item in data["issues"]),
                    "autoResolvedCount": len(data["autoResolved"]),
                    "riskRetainedCount": len(data["riskRetained"]),
                    "summary": data["summary"],
                })
            report_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"mode": mode, "output": str(output), "issues": len(data["issues"]),
                          "evidence": len(evidence), "bytes": output.stat().st_size}, ensure_ascii=False))
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
