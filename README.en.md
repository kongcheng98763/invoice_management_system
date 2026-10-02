# invoice_management_system

> ## Work in progress, features incomplete
>
> Only implemented so far: train e-ticket field extraction and invoice-type classification. Field extraction for other invoice types, multi-page PDFs, archiving and reconciliation are all still missing.
> Function signatures and the returned dict shapes may still change; check the code before relying on them.

[中文文档](README.md)

Reads key fields off invoice PDFs with pdfplumber. **Windows only** — not verified on other platforms.
- **Single-page PDFs only**: `__main__` warns and bails out when the page count exceeds 1; per-page routing for multi-page files is not implemented.

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

Both scripts follow the same flow: `page.extract_text()` for the whole page → `unicodedata.normalize("NFKC", text)` → line-by-line regex for the fields, with `page.crop(box)` for regions whose layout is fixed and whose text runs together.

Normalisation: characters coming out of a PDF are often Kangxi-radical compatibility forms (e.g. `⼦` U+2FBA); unless they are folded back to `子` U+5B50 no regex will match.

### `train_invoice_recognition.py`

`extract_invoice_train(page)`: extracts the fields of a train e-ticket. Takes a `pdfplumber.Page`, returns a `dict`.

| Key | Meaning | Notes |
|---|---|---|
| `is_train` | whether this is a train ticket | decided by the presence of the e-ticket number on the page; when `False` the returned dict **contains only this key**, so callers must check it before routing |
| `E-ticket_number` | e-ticket number | 25 digits |
| `invoice_no` | invoice number | 8–20 digits, accepts both the `发票号码` and `No.` prefixes |
| `invoice_date` / `invoice_time` | travel date / departure time | e.g. `2026年04月17日` + `11:54` |
| `total_amount` | total incl. tax (lowercase figure) | e.g. `¥39.00` |
| `id` / `person` | masked ID number / passenger name | ID shaped like `1234567890****123X` |
| `site` | `[origin_station, destination_station]` | first line of each of two fixed crop boxes; the coordinates were measured against the sample ticket at hand, so a new layout means re-measuring |

Caveats:

- When the date/time or station extraction fails the function **returns a partially filled dict**; later keys may be missing or `None`, so don't assume they are all present.
- Failures are reported through `tkinter.messagebox.showwarning`; this will be revisited, and switched to logging, once the GUI is built.

### `Regular_invoice_recognition.py`

`extract_invoice_normal(page)`: classifies the invoice type. Takes a `pdfplumber.Page`, returns a `dict`.

| Key | Meaning | Notes |
|---|---|---|
| `type` | invoice type | `"regular_invoice"` = electronic ordinary invoice (电子发票（普通发票）); `"vat_special_invoice"` = electronic VAT special invoice (电子发票（增值税专用发票）); `None` = no known type matched |

Caveats:

- **Only the type classification is implemented so far**; invoice date, invoice number and the remaining fields are still missing.
- Classification matches the page title 「电子发票（普通发票）/（增值税专用发票）」, accepting both full-width and half-width parentheses; reworded titles or new invoice types require new rules.
- When nothing matches, the function returns `{"type": None}` and shows a warning, so callers must check `type` first.
