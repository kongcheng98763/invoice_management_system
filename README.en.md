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
| `is_train` | whether this is a train ticket | decided by the presence of the e-ticket number on the page; when `False` the returned dict **contains only this key**, so callers must check it before routing |
| `e_ticket_number` | e-ticket number | 25 digits (the docstring above the function says `E_ticket_number`; the lowercase key is what is actually set) |
| `invoice_no` | invoice number | 8–20 digits, accepts both the `发票号码` and `No.` prefixes |
| `invoice_date` / `invoice_time` | travel date / departure time | e.g. `2026年04月17日` + `11:54` |
| `total_amount` | total incl. tax (lowercase figure) | e.g. `¥39.00` |
| `id` / `person` | masked ID number / passenger name | ID shaped like `1234567890****123X` |
| `site` | `[origin_station, destination_station]` | first line of each of two fixed crop boxes; the coordinates were measured against the sample ticket at hand, so a new layout means re-measuring |

Caveats:

- When the date/time or station extraction fails the function **returns a partially filled dict**; later keys may be missing or `None`, so don't assume they are all present.
- Failures are reported through `tkinter.messagebox.showwarning`; this becomes logging once the GUI is built.

### `normal_invoice_recognition.py`

`extract_invoice_normal(page)`: only checks whether the page carries the wording 「电子发票」, which keeps non-invoice PDFs out. Returns a `dict`.

| Key | Meaning | Notes |
|---|---|---|
| `is_invoice` | whether this is an electronic invoice | matches `\s*电\s*子\s*发\s*票` line by line (after NFKC); tickets whose title does not contain those four consecutive characters (e.g. a bare 「增值税电子专用发票」) come back `False` |

### `regular_invoice_recognition.py`

`extract_invoice_regular(page)`: classifies the invoice type, then reads the issue date and invoice number from the cropped header region. Returns a `dict`.

| Key | Meaning | Notes |
|---|---|---|
| `type` | invoice type | `"regular_invoice"` = electronic ordinary invoice (电子发票（普通发票）); `"vat_special_invoice"` = electronic VAT special invoice (电子发票（增值税专用发票）); `None` = no known type, in which case **this is the only key returned** |
| `invoice_date` | issue date | matched as `开票日期：2026年9月2日` inside the header crop box `(432, 17, page.width, 69)` |
| `invoice_number` | invoice number | 20 digits, same crop box |

Caveats:

- Classification matches the page title 「电子发票（普通发票）/（增值税专用发票）」, accepting both full-width and half-width parentheses; reworded titles or new invoice types require new rules.
- When `type` is `None` the function stops before the date/number step, so callers must check `type` first.
- A missing date or number yields `None` plus a warning rather than an exception.

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

Routing priority: `is_train` → 火车票; `type` = `regular_invoice` / `vat_special_invoice` → 普通发票 / 增值税发票; `is_invoice` true but type unknown → 其他发票; `is_invoice` false → not an electronic invoice, nothing is copied and the file is only listed in the log. Multi-page PDFs go to the `多页文件` subfolder of their category; name clashes get `(1)(2)` suffixes.

Caveats:

- During a batch scan the extraction functions' popup warnings are temporarily silenced — one dialog per ticket would block the run — and problems are reported in the log instead.
- PDFs that cannot be opened are listed under 无法解析 and not copied.
