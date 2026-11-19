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
| `answerId` | yes | Exact answer-row ID expected by the active CUS/KB build, e.g. `P-010-CUS-021.1`; never guess or renumber |
| `product` | yes | Product/model/configuration name shown in the navigation |
| `title` | yes | Short question heading |
| `question` | yes | Plain-language explanation of what the customer is deciding |
| `ledgerQuestion` | recommended | Exact customer question to write back to the CUS sheet; defaults should be set to `question` by the author |
| `originalValues` | yes | Array of `[label, value]` pairs preserving the competing/source values |
| `sourceName` | yes | Original filename shown on the page and exported to CSV |
| `sourceLocation` | yes | Relative path in the screening package and/or page/slide, table/row, or section location; shown on the page and exported to CSV (never use local disk or vault absolute paths) |
| `sourcePath` | optional | Relative path inside the screening package when kept separate from `sourceLocation`; shown on the page alongside `sourceName` and `sourceLocation` |
| `fields` | yes | Array of correction fields `{key, label, placeholder?}`; may be empty if no correction is needed |
| `choices` | yes | Array `{value, label, detail, requiresCorrection?}` tailored to this issue |
| `evidence` | yes | One or more screenshots `{caption, imagePath}`; screenshots must already be cropped and highlighted |
| `priorReviewRequired` | optional | Internal note only; stripped by `build_review_page.py` and never embedded in the customer HTML |

If a choice needs the customer to provide at least one correction field before the answer counts as complete, set `requiresCorrection: true`. The generic `revise` choice also requires a correction value. Do not set it for a choice such as “choose the Chinese version” when the chosen value is already explicit.

`imagePath` can be absolute or relative to `questions.json`. Use PNG, JPEG, WebP, or GIF. Make one screenshot per relevant table area rather than a whole-document screenshot. Captions should identify the source and its page/section, for example `英文规格书 · PDF 第 6 页 · P1.56 列`.

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

For compatibility with `compile-enterprise-knowledge/scripts/apply_answers.py`, keep the default headers and use the exact current CUS `answerId`s. The reader primarily matches `编号` and reads `回答（保留 / 不用 / 文字）`; the other columns preserve context for people.

## Customer feedback data

The JSON export contains:

- `schema`, `projectId`, `reviewRevision`, and export time;
- each stable `id` and `answerId`, selected choice, correction values, applicability, note, and rendered `answerText`;
- the exact question, source filename, and source location for traceability.

The CSV export contains one row per issue, including unanswered items with an empty answer. Keep the file's CUS IDs and header row intact; blank rows are intentionally ignored when the knowledge-base workflow reads answers.
