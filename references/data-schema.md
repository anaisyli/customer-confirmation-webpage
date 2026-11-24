# Question data format

`questions.json` is the private build input. The builder embeds it into one HTML file and replaces each evidence `imagePath` with an inline data URL. Keep the JSON and original evidence images in the internal run folder; do not send them to the customer.

## Root object

| Field | Required | Meaning |
|---|---|---|
| `schema` | recommended | `customer-review-input.v1` |
| `projectId` | yes | Stable customer/project identifier; use the existing knowledge-base project ID |
| `revision` | yes | Unique review revision, for example `20261119-1`; change it when questions/options change |
| `company` | yes | Customer-facing company name |
| `createdAt` | optional | Date shown in page header, preferably `YYYY-MM-DD` |
| `title` | optional | Page title; defaults to `客户资料确认` |
| `instructions` | optional | Short customer-facing instruction paragraph |
| `exportPrefix` | optional | Download filename prefix; defaults to `projectId` |
| `csv` | optional | CSV header/field mapping; default matches compile-enterprise-knowledge |
| `issues` | yes | Ordered list of approved items to ask the customer |

Each `issues[]` entry contains:

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Stable internal item ID, e.g. `P-010-CUS-021` |
| `cusId` | optional | Parent CUS ID shared by multiple cards. Use a distinct card `id` for each independently answered row. |
| `answerId` | yes | Exact answer-row ID expected by the active CUS/KB build, e.g. `P-010-CUS-021.1`; never guess or renumber |
| `linkedAnswers` | optional | Identical decisions only. Array of additional source rows with exact `answerId`, `ledgerQuestion`, `sourceName`, `sourceLocation`. Display all covered sources on the card. A shared reply expands to all rows in CSV. The `exception` choice keeps the details in JSON and exports blank CSV answers for this card. |
| `kind` | optional | `fact` (default), `usage` for file/image/page permissions, or `replacement` for missing or updated materials |
| `product` | yes | Product/model/configuration name shown in the navigation |
| `title` | yes | Short question heading |
| `question` | yes | Plain-language explanation of what the customer is deciding |
| `ledgerQuestion` | recommended | Exact customer question to write back to the CUS sheet; defaults should be set to `question` by the author |
| `originalValues` | yes | Array of `[label, value]` pairs preserving source values or identifying the material and reviewed scope. Use an empty array when the evidence identifies everything. |
| `sourceName` | yes | Original filename or webpage title. For web evidence the card displays the per-source titles instead. Exported to CSV |
| `sourceLocation` | yes | Location context for the CSV, such as a document page, webpage URL or section. Keep CSV column names compatible with the active build. Never use local disk or vault paths |
| `sourcePath` | required for package files | Exact path relative to the screening-package root, with filename. The page labels it `初筛包位置`. For multiple source files, separate each package-relative path with `；`. Never put an absolute, drive-rooted, UNC or vault path here. Omit it for website-only sources. |
| `fields` | yes | Array of correction fields `{key, label, placeholder?}`; may be empty if no correction is needed |
| `choices` | yes | Array `{value, label, detail, requiresCorrection?}` tailored to this issue. Optional `csvAnswer` overrides the answer cell only for that choice and accepts `保留`, `不用` or an empty string. JSON retains the full reply. |
| `evidence` | yes | For a file, use `{caption, imagePath}` or `{caption, sourceName, location, excerpt}` with a verbatim extract. For a webpage, add `sourceUrl`, `sourceName`, `location`, `capturedAt` and a screenshot or extract. A usage card may use `{caption, sourceName, location}` when verified identification is sufficient. Set `highlighted: false` on an unmarked image and use `highlights` for literal fragments of an excerpt. |
| `priorReviewRequired` | optional | Internal note only; stripped by `build_review_page.py` and never embedded in the customer HTML |

If a choice needs the customer to provide at least one correction field before the answer counts as complete, set `requiresCorrection: true`. The generic `revise` choice also requires a correction value. Do not set it for a choice such as “choose the Chinese version” when the chosen value is already explicit.

Set `correctionField` to an existing field key when a choice must show and require that field only. For example, a group exception can require a field for the affected file, page and decision. Fields dedicated to a choice are omitted from the general correction form.

`imagePath` can be absolute or relative to `questions.json`. Use PNG, JPEG, WebP, or GIF. Make one screenshot per relevant table area rather than a whole-document screenshot. Captions should identify the source and its page/section, for example `英文规格书 · PDF 第 6 页 · P1.56 列`.

For website evidence, add one item per page or relevant section. Use the date of the verified capture and include enough original text to place the value in context. A quotation is displayed as text and an original URL is opened only when clicked. The review HTML still opens offline.

## Minimal example

```json
{
  "schema": "customer-review-input.v1",
  "projectId": "P-010",
  "revision": "20261119-1",
  "company": "示例公司",
  "createdAt": "2026-11-19",
  "title": "产品资料确认",
  "instructions": "请看标亮截图并选择处理方式。需要修改时填写正确值。",
  "exportPrefix": "P-010",
  "issues": [
    {
      "id": "P-010-CUS-021",
      "answerId": "P-010-CUS-021.1",
      "product": "示例产品 · Model-A",
      "title": "整机尺寸采用哪一版？",
      "question": "中文和英文规格书写了不同尺寸。请选定对外使用的一版。",
      "ledgerQuestion": "Model-A 的整机尺寸采用中文还是英文规格？",
      "originalValues": [
        ["中文规格书", "120 × 80 × 30 mm"],
        ["英文规格书", "120 × 85 × 30 mm"]
      ],
      "sourceName": "Model-A-规格书.pdf",
      "sourcePath": "01_产品规格书/Model-A/Model-A-规格书.pdf",
      "sourceLocation": "PDF 第 4 页，尺寸规格表，Unit Dimension 行",
      "fields": [
        {"key": "dimensions", "label": "正确整机尺寸", "placeholder": "请写长 × 宽 × 深，并注明单位"}
      ],
      "choices": [
        {"value": "version_cn", "label": "采用中文尺寸", "detail": "120 × 80 × 30 mm"},
        {"value": "version_en", "label": "采用英文尺寸", "detail": "120 × 85 × 30 mm"},
        {"value": "revise", "label": "按我填写的内容修改", "detail": "请填写正确数值和单位", "requiresCorrection": true},
        {"value": "exclude", "label": "暂不使用此参数", "detail": "对外资料暂不采用"},
        {"value": "pending", "label": "还需要内部确认", "detail": "请说明待确认事项或预计回复时间"}
      ],
      "evidence": [
        {"caption": "中文规格书 · PDF 第 4 页 · Unit Dimension", "imagePath": "evidence/model-a-cn-p4.png"},
        {"caption": "英文规格书 · PDF 第 4 页 · Unit Dimension", "imagePath": "evidence/model-a-en-p4.png"}
      ]
    }
  ]
}
```

Default CSV headers and answer mapping:

```json
{
  "csv": {
    "headers": ["编号", "要确认的问题", "原文件名", "在初筛包里的位置", "回答（保留 / 不用 / 文字）"],
    "columns": ["answerId", "question", "sourceName", "sourceLocation", "answerText"]
  }
}
```

For compatibility with `compile-enterprise-knowledge/scripts/apply_answers.py`, copy the active CSV headers and answer IDs, including labels such as `问题001`. The current location header may be `在文件里的位置`. For a binary row use an empty `csvAnswer` for partial approval or deferral and collect its details in JSON. For a text row omit the override so the exported answer carries the approved scope and the customer's notes.

## Customer feedback data

The JSON export contains:

- `schema`, `projectId`, `reviewRevision`, and export time;
- each stable `id` and `answerId`, selected choice, correction values, applicability, note, and rendered `answerText`;
- the exact question, source filename, and source location for traceability.

The CSV export contains one row per issue, including unanswered items with an empty answer. Keep the file's CUS IDs and header row intact; blank rows are intentionally ignored when the knowledge-base workflow reads answers.

---

# Internal governance review format (`--mode internal`)

Use `--mode internal` for **internal-only** MAT / ANM / TERM / D / CUS triage pages. Internal review pages are strictly isolated from customer delivery: `build_review_page.py` refuses to build internal payloads in `customer` mode, refuses to write internal HTML into `交付客户/` (or `customer-delivery` / `to-customer`), embeds a top warning banner (`内部审核专用 · 严禁发给客户`), and exports `governance-decisions.v1` JSON instead of customer CSV.

## Internal root object

| Field | Required | Meaning |
|---|---|---|
| `schema` | yes (or `mode: "internal"`) | `internal-review-input.v1` (at least one of `schema: "internal-review-input.v1"` or `mode: "internal"` is required to prevent customer JSON from being built in internal mode) |
| `mode` | optional (or `schema`) | `internal` (rejects `customer`, `customer-review-input.v1`, or root `csv`) |
| `projectId` | yes | Stable project ID (e.g. `P-010`) |
| `revision` | yes | Unique review revision (e.g. `20261124-1`) |
| `company` | optional | Company name |
| `createdAt` | optional | Date string (`YYYY-MM-DD`) |
| `title` | optional | Page title; defaults to `内部待办决策页` |
| `instructions` | optional | Short instruction line shown under KPI cards |
| `exportPrefix` | optional | Export filename prefix; defaults to `projectId` |
| `summary` | optional | Dual-count KPI overrides: `{actionableGroups?, autoResolvedGroups?, riskRetainedGroups?, cusPendingGroups?, totalEvidenceCount?}`. Auto-computed if omitted. |
| `issues` | yes (may be empty if `autoResolved` or `riskRetained` is non-empty) | Ordered list of actionable internal decision groups |
| `autoResolved` | optional | Collapsed audit records closed by versioned rules: `[{id, title, ledger?, category?, ruleVersion?, evidenceCount?, reason?, members?}]` |
| `riskRetained` | optional | Collapsed risk-retained records (still carry ⚠, excluded from todo count): `[{id, ledger?, title, category?, evidenceCount?, reason?, riskNote?, vaultLinks?, members?}]` |
| `cusSummary` | optional | Overview of CUS batch status: `{draftGroups?, sentGroups?, totalGroups?, items?: [{id, title, status?, batchId?, kind?, evidenceCount?}]}` |

## Internal `issues[]` entry (one decision group)

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Stable group ID (e.g. `P-010-MAT-007-G1`, `P-010-ANM-001`) |
| `rootKey` | optional | Stable root-cause key carried straight into `governance-decisions.json`; defaults to `id` |
| `ledger` | optional | `MAT`, `ANM`, `CUS`, `TERM`, `D`, or `OTHER` (defaults to `ANM`) |
| `category` | optional | Short category label (e.g. `词项审核`, `尺寸图型号`, `表格精校`, `D类文件`) |
| `product` / `scope` | optional | Scope or product line label |
| `title` | yes | Short group summary |
| `question` | yes | What internal decision is needed and why |
| `evidenceCount` | optional | Total evidence items covered by this group (pages/terms/drawings); defaults to `members.length` or `evidence.length` |
| `recommendedAction` | optional | Choice `value` recommended by rules/Agent (must match a choice in `choices[]`) |
| `recommendedReason` | optional | Why the system recommends that action |
| `impactSummary` | optional | Human-readable impact summary (e.g. `影响 142 个文本块，涉及 18 份资料`) |
| `ruleVersion` | optional | Rule/dictionary version string |
| `owner` | optional | Internal owner (e.g. `知识库专员`) |
| `internalNote` | optional | Internal review context (rendered in internal mode only; forbidden in customer mode) |
| `sourceName` / `sourceLocation` / `sourcePath` | optional | Internal source reference (vault or package paths allowed in internal mode) |
| `vaultLinks` | optional | Array of `{label, path}` (or plain path strings) referencing vault notes |
| `originalValues` | optional | Array of `[label, value]` pairs |
| `members` | optional | Group evidence items supporting bulk select + per-item exclusion: `[{memberId, label, detail?, sourceName?, location?, imagePath?, selected?: true, meta?: [[k, v]]}]`. `imagePath` is embedded as Base64 and zoomable. |
| `evidence` | optional | Representative images/excerpts/URLs (`{caption, imagePath?, excerpt?, highlights?, sourceUrl?, sourceName?, location?, capturedAt?}`). Each issue must provide at least one of `evidence`, `members`, or `originalValues`. |
| `fields` | optional | Additional fields `{key, label, placeholder?}` (e.g. confirmed model name or customer-facing CUS question) |
| `choices` | yes | Array `{value, label, detail, targetStatus?, requiresCorrection?, correctionField?}` |

## Internal decisions export (`governance-decisions.v1`)

Exported from the internal HTML page for `governance.py collect` (and re-importable into the same HTML revision):

```json
{
  "schema": "governance-decisions.v1",
  "mode": "internal",
  "projectId": "P-010",
  "reviewRevision": "20261124-1",
  "exportedAt": "2026-11-24T03:00:00.000Z",
  "reviewer": {"name": "知识库专员", "team": "知识库治理"},
  "summary": {"totalGroups": 3, "decidedGroups": 3, "undecidedGroups": 0},
  "decisions": [
    {
      "id": "P-010-MAT-007-G1",
      "rootKey": "dimension_drawing_model:neighbor_title",
      "ledger": "MAT",
      "category": "尺寸图型号",
      "decision": "confirm_model",
      "decisionLabel": "按相邻规格标题批量确认型号",
      "targetStatus": "closed",
      "ruleVersion": "v1",
      "selectedMembers": ["item-01|page 3", "item-02|page 5"],
      "excludedMembers": ["item-03|page 8"],
      "values": {},
      "applicability": "",
      "note": "第 8 页为通用框架图，排除后单独保留待确认",
      "updatedAt": "2026-11-24T02:58:00.000Z"
    }
  ]
}
```
