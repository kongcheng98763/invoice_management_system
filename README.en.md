# invoice_management_system

> ## Work in progress, features incomplete
>
> Implemented so far: train e-ticket field extraction, invoice-type classification with issue date / invoice number, and collecting PDFs by invoice type.
> Field extraction for other invoice types, per-page parsing and reconciliation against the reimbursement ledger are all still missing.
> Function signatures and the returned dict shapes may still change; check the code before relying on them.

[中文文档](README.md)

Reads key fields off invoice PDFs with pdfplumber. **Windows only** — not verified on other platforms.
- **Extraction functions handle a single page**: the collector classifies multi-page PDFs from page 1 and drops the copy into a `多页文件` subfolder of its category; per-page parsing is not implemented yet.

## 2. Environment setup (uv)

### 1. Install uv

Run this directly in PowerShell:

```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

WinGet also works (this project uses 0.12.x) — mind the version:

```powershell
winget install --id=astral-sh.uv -e
```

Confirm with `uv --version` (this project uses uv 0.12.x).

### 2. Initialise the module environment

```powershell
cd invoice_recognition
uv sync
```

`uv sync` reads `pyproject.toml` + `uv.lock` and handles:

- downloading a Python interpreter when none satisfies `>=3.12`
- installing the locked dependencies: `pdfplumber 0.11.10` (plus `pdfminer-six`, `pypdfium2`, `pillow`, `charset-normalizer`, `cryptography`).

---

## 3. Extraction scripts

The extraction functions share one flow: `page.extract_text()` for the whole page → `unicodedata.normalize("NFKC", text)` → line-by-line regex for the fields, with `page.crop(box)` for regions whose layout is fixed and whose text runs together.

Normalisation: characters coming out of a PDF are often Kangxi-radical compatibility forms (e.g. `⼦` U+2FBA); unless they are folded back to `子` U+5B50 no regex will match.

### `train_invoice_recognition.py`

`extract_invoice_train(page)`: extracts the fields of a train e-ticket. Takes a `pdfplumber.Page`, returns a `dict`.

| Key | Meaning | Notes |
|---|---|---|
| `is_train` | whether this is a train ticket | matched as the title 「电子发票（铁路电子客票）」 inside the crop box `(89, 8, (418 + page.width) / 2, 38)`; when `False` the returned dict **contains only this key**, so callers must check it before routing |
| `e_ticket_number` | e-ticket number | the 25 characters following 「电子客票号」 |
| `state` | ticket state | the text right after the e-ticket number, e.g. `退票` (refunded), `补票` (reissued), `始发改签` (rebooked at origin); `None` when absent |
| `invoice_no` | invoice number | 8–20 digits, accepts both the `发票号码` and `No.` prefixes |
| `invoice_date` / `invoice_time` | travel date / departure time | e.g. `2026年04月17日` + `11:54` |
| `total_amount` | total incl. tax (lowercase figure) | e.g. `¥39.00` |
| `id` / `person` | masked ID number / passenger name | ID shaped like `1234567890****123X` |
| `site` | `[origin_station, destination_station]` | first line of each of two fixed crop boxes; the coordinates were measured against the sample ticket at hand, so a new layout means re-measuring |

Caveats:

- Three text sources are in play: `is_train` reads a cropped title box, `e_ticket_number`/`state` come from `extract_text(use_text_flow=True)`, the rest from the default reading-order text; all three normalise first.
- When the date/time extraction fails the function **returns a partially filled dict**; later keys may be missing or `None`, so don't assume they are all present.
- Failures are reported through `tkinter.messagebox.showwarning`; this becomes logging once the GUI is built.

### `regular_invoice_recognition.py`

`extract_invoice_regular(page)`: tells whether the page is an electronic ordinary invoice, and if so reads the issue date and invoice number. Returns a `dict`.

| Key | Meaning | Notes |
|---|---|---|
| `is_regular` | whether this is an ordinary invoice | matched as 「电子发票（普通发票）」 inside the title crop box `(128, 10, 416, 49)`, full-width and half-width parentheses both accepted; when `False` **this is the only key returned** |
| `invoice_date` | issue date | matched as `开票日期：2026年9月2日` inside the header crop box `(432, 17, page.width, 69)` |
| `invoice_number` | invoice number | 20 digits, same crop box |

Caveats:

- When `is_regular` is `False` the function stops before the date/number step, so callers must check it first.
- A missing date or number yields `None` plus a warning rather than an exception.
- Reworded titles or new invoice types require new rules.

### `VAT_invoice_recognition.py`

`extract_invoice_VAT(page)`: tells whether the page is an electronic VAT special invoice. Returns a `dict`.

| Key | Meaning | Notes |
|---|---|---|
| `is_vat` | whether this is a VAT special invoice | matched as 「电子发票（增值税专用发票）」 inside the title crop box `(121, 14, 432, 52)`; when `False` **this is the only key returned** |

Caveats:

- **Only the keyword check exists**; the fields after the `# 2.` marker in the function body (date, number, …) are not implemented.
- The former combined `type` check has been split into the `is_regular` and `is_vat` modules, and `normal_invoice_recognition.py` (the 「电子发票」 wording screen) was deleted — that screen now lives in `classify_invoices.py` as `ELECTRONIC_INVOICE_RE`.

### `classify_invoices.py`

The collector: pick a folder, it walks the PDFs underneath, classifies each one from its first page and copies the **duplicates** (originals stay put) into `invoice_recognition/temp/发票分类/`:

```
发票分类/
├── 火车票/
│   └── 多页文件/
├── 增值税发票/
│   └── 多页文件/
├── 普通发票/
│   └── 多页文件/
└── 其他发票/
    └── 多页文件/
```

Routing order: `ELECTRONIC_INVOICE_RE` (the 「电子发票」 wording, checked line by line over the whole page) acts as the gate that drops irrelevant PDFs first, then the page is asked in turn — `is_train` → 火车票, `is_vat` → 增值税发票, `is_regular` → 普通发票; anything that passed the gate but matches none of the three → 其他发票. Files failing the gate are not copied, only listed in the log. Multi-page PDFs go to the `多页文件` subfolder of their category; name clashes get `(1)(2)` suffixes.

Caveats:

- The scan runs on a worker thread and feeds the log through a queue; blocking the main thread per PDF is what made the window show "not responding" while dragging.
- During a batch scan the extraction functions' popup warnings are temporarily silenced — one dialog per ticket would stall the run, and Tk cannot be called off the main thread — problems are reported in the log instead.
- PDFs that cannot be opened are listed under 无法解析 and not copied.
- pdfminer emits `Could not get FontBBox …` for fonts whose descriptor lacks FontBBox; measured, text extraction is unaffected, and a `_QuietFontBBox` filter hides only that message.
