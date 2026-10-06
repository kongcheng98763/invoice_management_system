# invoice_management_system

> ## Work in progress, features incomplete
>
> Implemented so far: train e-ticket field extraction; for electronic ordinary and VAT special invoices — issue date, invoice number, line items, buyer/seller, total incl. tax and remarks; per-page parsing of multi-page PDFs with cross-page line wrapping stitched back; collecting PDFs by invoice type; and a local FastAPI service for the UI to talk to; on the UI side, scanning a folder and **dropping files from WeChat / Explorer into the drop zone**.
> Still missing: the SQLite ledger and reconciliation against the archive folders / 报销表. The `winui\` frontend does build and run now (self-contained, see section 5), but the ledger page and the home stat cards are placeholders.
> Function signatures and the returned dict shapes may still change; check the code before relying on them.
> Two scope limits up front: **one PDF is parsed as one ticket** (concatenating several mixes the fields, though a page-number restart now raises a warning), and `invoice_date` is the *travel* date on train tickets but the *issue* date on the other two types.

[中文文档](README.md)

Reads key fields off invoice PDFs with pdfplumber. **Windows only** — not verified on other platforms.
- **Page-level extractors take a single `page`**; for multi-page tickets use `extract_invoice_regular_pages(pages)` / `extract_invoice_VAT_pages(pages)`, which parse page by page and stitch wrapped item names across pages.

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
- installing the locked dependencies: `pdfplumber 0.11.10` (plus `pdfminer-six`, `pypdfium2`, `pillow`, `charset-normalizer`, `cryptography`), and `fastapi` + `uvicorn[standard]` for the local service.

---

## 3. Extraction scripts

The extraction functions share one flow: `page.extract_text()` for the whole page → `unicodedata.normalize("NFKC", text)` → line-by-line regex for the fields, with `page.crop(box)` for regions whose layout is fixed and whose text runs together.

Every crop is made with `strict=False`: pdfplumber by default demands the crop box to lie entirely inside the page, while 136 of the 674 sampled pages have table cells drawn slightly past the page edge (train tickets are the typical case, `y0 = -0.002`), which the default raises `ValueError` on — inside the scan pipeline that means a ticket whose category was already recognised gets reported as unreadable and is not archived. Clamping the box back loses no text, since the ticket content is inside the page anyway.

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
| `total_amount` | total incl. tax (lowercase figure) | e.g. `39.00`; the `¥` printed on the ticket is stripped so every ticket type returns the same shape, and the UI adds the symbol back |
| `id` / `person` | masked ID number / passenger name | ID shaped like `1234567890****123X` |
| `site` | `[origin_station, destination_station]` | first line of each of two fixed crop boxes; the coordinates were measured against the sample ticket at hand, so a new layout means re-measuring. An empty crop logs 「站点信息提取失败」 and returns early — the emptiness test has to happen before the text is defaulted to `""`, otherwise the warning is unreachable |

Caveats:

- Three text sources are in play: `is_train` reads a cropped title box, `e_ticket_number`/`state` come from `extract_text(use_text_flow=True)`, the rest from the default reading-order text; all three normalise first.
- When the date/time extraction fails the function **returns a partially filled dict**; later keys may be missing or `None`, so don't assume they are all present.
- Failures go through `invoice_item_recognition.warn()`: the message is only appended to the warning list, never shown as a dialog — how it surfaces is up to the caller.

### `regular_invoice_recognition.py`

`extract_invoice_regular(page)`: tells whether the page is an electronic ordinary invoice, and if so reads the issue date, the invoice number and the whole set of fields from `invoice_item_recognition`. Returns a `dict`. For multi-page PDFs use `extract_invoice_regular_pages(pages)`.

| Key | Meaning | Notes |
|---|---|---|
| `is_regular` | whether this is an ordinary invoice | matched as 「电子发票（普通发票）」 inside the title crop box `(128, 10, 416, 49)`, full-width and half-width parentheses both accepted; when `False` **this is the only key returned** |
| `invoice_date` | issue date | matched as `开票日期：2026年9月2日` inside the header crop box `(432, 17, page.width, 69)` |
| `invoice_number` | invoice number | 20 digits, same crop box |
| remaining keys | line items, buyer/seller, totals, remarks | see `invoice_item_recognition.py` below |

Caveats:

- When `is_regular` is `False` the function stops before the date/number step, so callers must check it first.
- A missing date or number yields `None` plus one entry in the warning list, never an exception.
- Reworded titles or new invoice types require new rules.

### `invoice_item_recognition.py`

The line-item area has the same layout on electronic ordinary invoices and electronic VAT special invoices, so the parsing lives in this one module and both type modules call it.

`extract_invoice_item_fields(page)`: one page. `extract_invoice_items(pages)`: a whole document, including cross-page stitching.

| Key | Meaning | Notes |
|---|---|---|
| `item_list` / `records` | line items | eight columns per item: `item_name` `spec` `unit` `quantity` `unit_price` `amount` `tax_rate` `tax_amount`; a column the ticket doesn't have comes back as an empty string. `records` is the per-page intermediate `[("row", InvoiceItem) / ("frag", {...})]`, already consumed by the document-level function |
| `buyer` / `seller` | buyer / seller | `{"name","tax_id"}`; taken from the second and the **last** cell of the main table's first row — ordinary invoices have a merged cell there, VAT special invoices don't, and the last cell works for both |
| `remark` | remarks | the row whose first cell is the 「备 注」 label — whitespace removed, the cell counts when 「备注」 shows up within its first three characters (an airline sample has the row above squeezed into the merged cell and reads 「姓 备 注」), while item names that merely contain 备 and 注 somewhere do not; multi-page remarks are joined in page order, and `tempemark_label_test.py` pins all six cases |
| `total_upper` / `total_amount` | total incl. tax, in words / in figures | the figure carries no `¥` and stops after two decimals, otherwise it would run into the codes in the remarks once whitespace is stripped |
| `amount_total` / `tax_total` | the 合计 row's amount / tax | located with the column bands measured from the header |
| `page_count` / `page_no` / `drawer` | 共X页 / 第Y页 / 开票人 | `None` when the ticket doesn't print it; `extract_invoice_items` watches whether `page_no` keeps increasing and warns when it restarts or repeats, which is what a PDF holding more than one ticket looks like (`temp\multi_page_boundary_test.py`) |

Caveats:

- Columns are found by accumulating header words left to right until they spell a column name, measuring each span, taking the midpoints as vertical bands and filing a data row's words by their centre x. A row counts as a complete item only when the amount and tax-rate bands both hold text; anything else is a wrapped continuation of the previous row — across pages too (`*电子元件*价外费用（配` on page 1 joined with `送费）` on page 2).
- Column positions are always measured from the header, never hardcoded: samples come in 595pt and 600pt page widths, and there are tickets without the 规格型号/单位 columns, with 单价 before 数量, or with the whole header shifted relative to the data.
- Some tickets fake bold by drawing every glyph twice; `dedupe_page` drops the redrawn characters first, otherwise even the numbers come out doubled (`9966..4466`).
- When the header is misaligned the amount falls into the tax-rate band as `96.4613%`; `realign_amount` splits it back out.
- 不征税/免税 rows legitimately leave the tax column empty, which is why only amount + tax rate are required.
- Bands are chosen by a word's centre, so a word straddling two columns (quantity or unit price overflowing its band) would be filed wrong — no sample does this yet.
- Rule changes must be diffed against the whole corpus, not just run once: `tempemark_snapshot.py` exports the 394 「remark / buyer & seller tax id」 results so a change can be compared byte for byte. Tightening the remark row to 「the cell equals 备注」 silently turned an airline ticket's remark into `None` (its cell actually reads 「姓 备 注」); allowing the label within the first three characters got the export back to zero differences.

### `VAT_invoice_recognition.py`

`extract_invoice_VAT(page)`: tells whether the page is an electronic VAT special invoice, and if so reads the issue date, the invoice number and the whole set of fields from `invoice_item_recognition`. Returns a `dict`. For multi-page PDFs use `extract_invoice_VAT_pages(pages)`.

| Key | Meaning | Notes |
|---|---|---|
| `is_vat` | whether this is a VAT special invoice | matched as 「电子发票（增值税专用发票）」 inside the title crop box `(121, 14, 432, 52)`; when `False` **this is the only key returned** |
| `invoice_date` / `invoice_number` | issue date / invoice number | tried in the header crop box first; on VAT samples the label and its value are often separated by the stamp, so it falls back to scanning the whole page for a 年月日 date and a 20-digit number |
| remaining keys | line items, buyer/seller, totals, remarks | see `invoice_item_recognition.py` above |

Caveats:

- The page-wide fallback recognises the invoice number as "20 consecutive digits", so a same-length code in the remarks could be picked up by mistake.
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
- The extraction functions' warnings are only collected during a scan, never shown as dialogs (Tk must not be touched off the main thread, and one dialog per ticket would stall the run); `_summarise` writes the list into the log at the end and raises a single summary dialog.
- PDFs that cannot be opened are listed under 无法解析 and not copied.
- pdfminer emits `Could not get FontBBox …` for fonts whose descriptor lacks FontBBox; measured, text extraction is unaffected, and a `_QuietFontBBox` filter hides only that message.
- Copying is serialised by `_COPY_LOCK`: the tkinter window and the service may archive into the same folder at the same time, and the duplicate-name check (`unique_path`) would otherwise pick the same `(1)` twice.
- `find_pdfs` skips the archive directory itself (and the default archive directory), so a second scan never re-reads the copies produced by the first one.
- `collect(search_root, out_dir, report, with_fields=False, files=None)` returns a 5-tuple `(counts, multi_page, non_invoices, unreadable, entries)`, where each entry is `{file, category, is_multi_page, note, fields}`. Only `with_fields=True` parses the ticket fields page by page (what the service needs); the tkinter window still just classifies, which stays faster. `files` exists for the drop zone: pass a list of paths to process exactly those, `None` or an empty list falls back to walking `search_root`.

## 4. Local service `invoice_service.py`

need.md 3.1 option A: a resident FastAPI process on the Python side, talked to by WinUI3 over REST + WebSocket.

```powershell
cd invoice_recognition
uv run uvicorn invoice_service:app --host 127.0.0.1 --port 8765
```

| Endpoint | Purpose |
|---|---|
| `GET /health` | returns `{status, out_dir, categories}` |
| `POST /invoices/classify` with `{"folder": <absolute path>, "files": [<path>…]}` | runs `collect()` to scan and archive (copies only, originals untouched); the response is a `ClassifyReport`, while log lines are pushed to `/ws` as the scan proceeds. A non-empty `files` processes exactly those paths (what the drop zone sends); an empty one falls back to walking `folder`, and if neither yields a usable target the call returns 400 |
| `WS /ws` | envelope `{"kind":"log","line":…}` and `{"kind":"done","report":…}` |
| `GET /invoices/classify/result` | 501 placeholder until the ledger lands |

Caveats:

- The drop path (`collect(..., files=[…])`) is covered by `temp\files_protocol_test.py` — 14 cases, the important one being the hard rule that originals are never touched: after a scan every source file must still have the same size, mtime and ctime. It also checks name clashes don't overwrite earlier copies, an empty list falls back to a folder walk, a copy already sitting in the archive folder is not archived a second time, pipes/device names are refused outright, and the per-request cap: the service's `MAX_FILES` and the client's `DropInbox.MaxFiles` must read the same number (the case parses both sources, so changing one side silently now fails), an over-cap batch is refused with 400 as a whole **and leaves no new copies in the archive**, and an at-cap batch of non-existent paths is accepted without writing any copies. Those two cases hit the **running** service, so the test first checks that its `out_dir` sits under the repo `temp\` (via `/health`) and fails outright otherwise: the `INVOICE_OUT_DIR` this process sets on import cannot affect a process that is already up, so pointing the service back at the real archive would make the test write copies into it.
- The service binds `127.0.0.1` only and does no authentication: whatever absolute path a client sends it will be read and copied into the archive. That is a deliberate trade-off for "one person, one machine, the UI is the only caller" — do not expose it on a routable interface.
- Malformed paths were probed and all degrade to a per-file 无法解析 entry rather than a 500: missing file, file deleted after the drop, the reserved name `C:\con.pdf`, a UNC share, a missing drive, and relative paths each affect only their own record, while WeChat-style long Chinese names with spaces and full-width brackets archive fine.
- Archiving stays fixed to `invoice_recognition\temp\发票分类`, the folder the tkinter window uses; set `INVOICE_OUT_DIR` to point it somewhere else for trial runs so you don't pile copies into the real archive.
- The scan runs on an `asyncio.to_thread` worker, so the broadcaster keeps the event loop captured at startup to post messages back to it.
- One scan at a time (`_scan_lock`): the warning list, the archive folder and the WebSocket broadcaster are process-wide, so concurrent scans would interleave logs and clash on duplicate file names.
- Known and deliberately unfixed (acceptable for one user on one machine — don't extend this to multi-user): the WebSocket broadcast has no per-session id, so a second window sees another client's log lines and its "扫描完成"; `_COPY_LOCK` is process-local, so running the tkinter window and two service instances together can still clash on a copy name; drop de-duplication compares strings, so a short name, a UNC path or a junction spelling of the same file counts twice.
- Two parser-scope limits worth knowing before extending anything: **one PDF is parsed as one ticket** (header fields come from page 1, the totals from the last page), so a PDF that concatenates several invoices — common when downloading receipts from mail — yields one record mixing ticket A's number with the last ticket's amount — `temp\corpus_recount.py` recounts the corpus **through the production extractors** (a bare "20 consecutive digits" guess under-reports: three multi-page VAT invoices were wrongly read as having no number, because `extract_text` does not put the label next to the digits): 14 of 662 sampled PDFs are multi-page, 4 of those are electronic invoices and each of the 4 resolves to exactly one invoice number across its pages; the other 10 are train tickets or layouts that print no such header, so only the page counter below covers them — the check that catches this is the printed 「共X页第Y页」 counter: when Y does not increase, `extract_invoice_items` warns that the file looks like more than one ticket instead of silently stitching them together; and `invoice_date` means the *travel* date on a train ticket but the *issue* date on the other two types, so a ledger column built on it drifts with the ticket type.
- Ticket fields hang off each `files` entry (`entry["fields"]`) instead of being flattened into a separate root-level `fields` list: the flat list could not be matched back to a file, so the ticket lines the UI printed said nothing about which invoice they came from.
- The response carries `warnings` (at most 50 entries) and `warning_count` (the real total) — the ticket warnings collected by this scan; the list is cleared before each run.
- Copies that already live in the archive folder are counted separately as `already_archived` and are **no longer folded into `non_invoices`**: dropping the archive folder alongside another folder used to tally those copies under 非发票（未复制）("not an invoice"), which reads like a recognition failure when the real decision was "recognised, but by design not archived a second time". The UI summary gains a 归档目录里的副本（不再重复归档）: N line whenever N > 0.
- `log` in the POST response is filled only when **no WebSocket client is subscribed**; otherwise those lines were already pushed and a second copy would print each line twice.

## 5. WinUI3 frontend `winui\`

`MainWindow` is a NavigationView + Frame shell with four pages: 首页, 报销台账, 发票识别, 设置. The 发票识别 page talks to the service above: pick a folder (or drop files in) → `POST invoices/classify` → show the four categories plus the live log. `Services\ApiClient.cs` / `Services\WsClient.cs` hardcode port 8765 and these paths, overridable on the settings page.

Drag and drop: the whole source-folder row (`DropZone`) is a drop target, so a PDF in Explorer — or a file received in a WeChat chat window — can be dragged onto it; the hint then reads 「已拖入 N 个 PDF」 and 开始分类 processes exactly those. The rules —

- a **single folder** dropped is the same as picking that folder: the whole tree is walked;
- dropped files (any number), or a mix of folders and files, are expanded into the PDFs inside the folders plus the loose files, and sent as `files`;
- a loose non-`.pdf` is rejected; when nothing usable arrives the hint says only `.pdf` files are handled (and a dropped folder is expanded for them);
- paths are read from `StorageItems` first (Explorer and WeChat both provide it); when that is missing the page falls back to `Text`, which is split on newlines and semicolons, unquoted, with `file://` turned back into a local path while `http` links and plain prose are discarded;
- individual items that fail to yield a path do **not** count as a complete list: `DropInbox.CollectPaths` asks for each path separately (every one is a cross-process call — the source may release its data object mid-way, or the item may be a recycle-bin entry or a cloud placeholder that never landed on disk), keeps what came back, counts what did not, and the page then adds 「注意：这次拖进来的文件清单里有 N 项取不到路径（清单里认出 X 项，加上文本兜底一共 Y 项）」 — the two counts stay separate so they cannot be quietly reconciled into a wrong total, and the reason quoted after them is the **first** failure (later items' errors do not overwrite it, so the line never blames the whole batch for what only the last item hit). The merge itself is `DropInbox.MergeTextFallback` rather than inline code. The `Text` fallback runs again in that case instead of being skipped because "we already got something", but its results are merged **de-duplicated case-insensitively**: the text usually repeats what the list already carried (a dropped folder arrives in both formats), and a duplicated folder would break `DropInbox`'s single-folder fast path — two identical entries means it falls onto the `files` list, hits the 2000 cap, and the source-folder box never gets filled. `await view.GetTextAsync()` is wrapped too: when the data object has already been released it throws, and an exception escaping here would discard even the items that came back fine, which shows up as "sometimes a drop just does nothing". An empty path (recycle bin, cloud placeholder) counts as a lost item rather than sneaking an empty string into the list, and when *every* item is empty the page says 「N 项都没给出可用的路径」 instead of blaming the list read.
- WeChat sends one file per drag, so consecutive single-file drops **accumulate**, de-duplicated by path — previously each drop replaced the list and only the last ticket ever got archived while the counters looked fine. `_droppedFiles`/`_dropSeq` are static like `LiveLog`, so a page rebuilt after navigating back re-announces 「累计 N 个 PDF 待处理（切页回来清单还在）」 rather than letting 开始分类 quietly scan the source folder.
- an unreadable folder is reported, not mistaken for an empty one: the client probes the root with a single `EnumerateFileSystemEntries` before taking the single-folder fast path (「拖进来的文件夹读不动：…」), while the service's `walk_files` hands every `scandir` `PermissionError` to `collect`, which logs 「[目录读不动，已跳过] path: reason」 for up to five directories plus how many were left unlisted. Checked by `temp\denied_dir_probe.py` (a directory denied with `icacls /deny`, 6 cases) and the matching `dropinbox_test` case.
- the review claim that `File.Exists` answers `False` beyond 260 characters — so a real PDF would be announced as "no PDF" — **does not reproduce on the UI side, but following it found the layer that really does break: the Python service**. `temp\longpath_manifest_probe.py` builds a 461-character path (only via the `\\?\` verbatim channel; an earlier probe could not create one with ordinary calls and wrongly concluded such paths do not exist here), then runs the same source compiled twice, once with `<longPathAware>` in the apphost and once without: with the system `LongPathsEnabled=0` both variants read, enumerate and `File.Exists` the file identically, and the shell (`SHCreateItemFromParsingName` → `SIGDN_FILESYSPATH`) returns the same file as an 8.3 short path (125 characters), which `File.Exists` still accepts. So the page accepts a long path happily — and then `temp\long_path_probe.py` posts that same 420-character path to the running service, where `os.path.exists` answers `False` and the entry comes back as 「不是可读取的普通文件」: **the UI took it, the service could not read it**, and a real ticket was filed under "unparseable" with no hint that the length was the cause. CPython is not a long-path-aware process and .NET's System.IO adds the prefix on its own while Python does not. `classify_invoices.fs_path()` now translates an incoming path before anything touches the disk, and **the two tiers are not the same number**: files switch at 260 (measured — `os.stat` still works at 259 and fails at exactly 260), directories switch early at 250, because `os.scandir` performs its first match with `directory\*`; a 256-character directory answers `True` to `os.path.exists` yet `scandir` reports "path not found", while 228 works, so 250 sits inside the measured pass/fail band. The switch happens **per entry** (`walk_files` asks `is_dir` for each one): converting only the root leaves "short root, long child" trees skipped wholesale as an unreadable directory. UNC becomes `\\?\UNC\server\share`; relative and drive-relative (`C:foo`) paths get no prefix — the verbatim channel only accepts absolute paths, prefixing those would make them invalid, so they still land in "unparseable". The prefix must also be stripped in two places or it eats a hard constraint and the log's readability: `_is_archive_copy` and `find_pdfs`' archive exclusion compare after `strip_verbatim` (`resolve()` keeps the prefix, so comparing it against a plain `out_dir` never matches and a >260 copy inside the archive gets archived again, violating "copies are never re-archived"), and everything sent to the UI goes through `shown()` so a `无法解析：…` line never leaks `\\?\C:\…`. The archive-write side uses the directory tier too: once the destination passes 260, `unique_path`'s existence probe answers "not there" for every candidate, so the copy never gets its `(1)` suffix and an existing copy would be overwritten — which breaks "copy only, never touch the previous copy". `temp\long_path_probe.py` (15 cases) covers it end to end — one long path, a deep child found by the folder walk, a long copy already inside the archive, "no verbatim prefix anywhere in the response (log and entries included)", plus the "original file untouched" invariant — and `temp\fs_path_edge.py` (10 cases) covers the boundaries: the 260 cell for files, the directory tier making `scandir` work, the prefix surviving pathlib, forward slashes, drive-relative input, short paths left alone, and an already-prefixed path not doubled. The `<longPathAware>` manifest entry stays, with a comment saying it is not the load-bearing piece here — it is what the Win32 path requires once the system flag is on. The `dropinbox_test` long-path case was upgraded accordingly from "must not be called 没有 PDF" to "must actually land in the list" (343-character fixture, `Files=1`).
- a 清除拖入 button appears next to the hint and puts the page back into folder-scan mode;
- editing the source folder clears a previous drop list automatically — `files` overrides `folder` on the service side, so without this a later folder scan would silently re-process the earlier dropped files;
- the four drag handlers are attached both to `DropZone` and to the inner `FolderPathBox` — the visible input box is where a user naturally aims, and WinUI gives no dependable guarantee that a drop hitting a child without `AllowDrop` bubbles to the ancestor that has it, so the path works under either semantics; every handler sets `e.Handled = true` so a bubbled event cannot be processed twice, and both elements lose `AllowDrop` while a scan runs.
- folder expansion runs on a background task, so the user may have clicked 清除 or edited the path before it returns; a `_dropSeq` counter voids that result instead of repopulating a list the user just cleared.
- scan locking is process-level: `_scanning` is static and every state change is broadcast to all live page instances, so a page rebuilt by navigating back immediately shows 分类中… and cannot start a second concurrent scan.
- the `Drop` handler wraps its async read in `e.GetDeferral()`: without it WinUI can mark the drop complete before the paths come back, which shows up as "sometimes a drop just does nothing".
- when a source offers neither a file list nor recognisable text, the caption next to the cursor says so outright (「这个来源没给出文件，只能收 PDF」) and the log lists the formats the source actually supplied — otherwise the user only sees a "no entry" glyph with no idea what was missing.
- dropped folders are expanded recursively, and a folder dropped twice (or a parent and its child together) counts each PDF once — `DropInbox` de-duplicates, otherwise the same ticket would be archived twice.
- an oversized drop is trimmed instead of rejected wholesale: the service caps `files` at 2000 and answers **400 for the entire batch** when it is exceeded, so nothing at all would be archived. The client therefore truncates to 2000 first and the hint/log say how many were left out. **Loose files the user picked are kept ahead of folder-expanded ones** — the other order would drop exactly the tickets someone hand-picked and keep an alphabetical prefix of the folder.
- a PDF WeChat has just received and is still writing can be dropped too: the UI keeps the path, the service's `open()` gets refused by the OS, and the entry says `无法解析：[Errno 13] Permission denied: …` and is counted under "unreadable (not copied)" instead of silently vanishing; once the lock is released the same ticket archives normally (`temp\locked_probe.ps1`, 5 checks, posts the same path before and after holding an exclusive handle).
- consecutive drops (WeChat only ever hands over one file) no longer cancel each other: `ApplyDropAsync` only **reads** `_dropSeq` before going to the background instead of incrementing it. Incrementing at the entry made a second drop invalidate the first one that was still waiting on the disk enumeration - and the log then blamed "the source folder changed or you pressed clear", a reason the user never did. Clearing and editing the source box are the two things allowed to bump the counter.
- the format probes are guarded too: `DataPackageView.Contains` is a cross-process question, and the source releasing the data object mid-drag throws `RO_E_CLOSED`. The hover side was already wrapped by `ProbeDraggable`; the read side (the two `Contains` calls in `ReadDroppedPathsAsync`) was bare - one throw dropped the entire batch, including items already collected, and skipped the text fallback. They now go through `ContainsSafe`, which degrades to "format not present" and records the reason.
- two spellings of the same file count as one: `DropInbox.Normalize` folds a path to one key first (forward slashes, `.\`, a trailing separator, the `\\?\` verbatim prefix) and every comparison is case-insensitive; `Plan` starts with `Distinct`, and `PathsFromText` / `MergeTextFallback` / the accumulated list dedupe on that key. Without it one invoice archives twice as `xxx (1).pdf`, and `C:\inv` plus `C:\inv\` count as two folders, which kills the "a single dropped folder = pick that directory" early exit and pushes the whole tree onto the capped `files` list. 8.3 short names are not expanded (that costs a Win32 call, and the shell only returns them for paths over 260, which the service can read anyway).
- drop results land on the page the user can actually see: `Loaded` records the instance in a static `_active`, and the continuation after `Task.Run` writes the hint, the button and the source box through `_active`. If the user switched pages while the folder was being enumerated, all of that used to go to an unloaded instance - the process-level list grew but the visible page kept the old number and 「清除拖入」 never appeared. For the same reason `WsClient`/`ScanStateChanged` subscriptions moved from the constructor to `Loaded`: the shell's Frame has `CacheSize=1`, so a cached page never re-runs its constructor and would stay deaf to pushes, stuck on 「分类中…」 with `DropZone.AllowDrop = false` - which looks exactly like "I can't drop anything here".
- the scan takes folder and file list at the same moment: `files` used to be read from the static list after the awaits, while the source box stayed typeable during the five-second websocket window, so a keystroke cleared the list and the wire carried "previous folder + empty list". A snapshot is taken on entry and used for the check, the log line and the request; 「粘贴文件」 is disabled during a scan for the same reason.
- when the service has not reported the archive dir, `DefaultArchiveDir` now looks for "which ancestor holds `invoice_recognition`" rather than "does `发票分类` exist yet": on the very first run that folder does not exist, and the fallback was a **relative** path, which `UnderAny` resolved against the process working directory - so the "copies in the archive dir are never re-archived" exclusion silently matched nothing.
- `ProbeDraggable` now answers **three states** (acceptable / genuinely no files / could not ask), and only a definite answer is latched into `_dragAcceptable`. A failed probe used to lock "not acceptable" for the whole drag - the field only resets on `DragLeave`/`Drop`, and with `AcceptedOperation.None` the `Drop` handler never runs, so even the explanatory log line never landed; the user just saw a prohibition cursor. Leaving `null` makes `DragOver` ask again next frame, so repeated probing only happens on the "source never answers" error path, and the caption now distinguishes the three cases.
- coverage is deliberately non-monotonic between the two paths: dropping a **single** folder hands the walk to the service, which puts **no cap on `folder`** (`MAX_FILES` only validates `files`), so 5000 tickets all get archived; adding one unrelated `.jpg` to that same drop pushes it onto the `files` path, where only the first 2000 survive. The client-side folder walk is the thing capped at 20 000 directories / 20 000 files.
- Recursive expansion has to survive junctions. A self-pointing junction made `Directory.EnumerateFiles(AllDirectories)` never finish, so `EnumeratePdfs` is now a hand-written BFS that remembers each directory by its real identity (`ResolveLinkTarget(returnFinalTarget: true)`, links resolve to what they point at) and caps the walk at 20 000 directories / 20 000 files, returning what it found plus a truncation note. The service-side folder walk got the same treatment: `find_pdfs` no longer uses `rglob` and refuses to descend into directory links — Windows `os.scandir` reports a junction as an ordinary directory (`os.path.islink` answers `False`), and only `entry.is_junction()` sees it; without that guard one PDF was counted **32 times**.
- The same rules are checked by `temp\junction_scan_probe.py`, which builds a real self-pointing junction and asserts the walk returns exactly one PDF.
- How a batch of paths is interpreted lives in `Services\DropInbox.cs`, which is pure computation with no control access; the page only writes the result into the UI. Its branches are covered by `temp\dropinbox_test`, which compiles that production file directly and asserts 33 cases (single folder, loose files, mixed, non-PDF only, link-only source, empty, a repeated drop, archive copies dropped from a mixed drop, a copy-only drop — twice, once on the counts and once on the hint, which must say 都是归档目录里已有的副本 and never 没有 PDF, two zero-PDF folder cases, a directory holding a self-pointing junction, a folder denied by `icacls /deny` (the hint must say 读不动, not 已选择拖入的文件夹), a 343-character long path that must actually be accepted, three per-drop-cap cases — exactly 2000 kept untouched, 2001 trimmed to 2000 asserting **which** file was dropped (the hand-picked loose PDF survives, a folder-expanded one is cut, rather than only matching on message text), a single big folder still taking the folder-walk path — four text-fallback cases (a semicolon list, a web link discarded, empty text, a repeated path), six `CollectPaths` cases for reading item paths one by one (all good, one throwing in the middle, all throwing, an empty path, an empty source, and: when several items fail differently the **first** reason is the one reported, not whatever failed last), and five `MergeTextFallback` cases (text repeating an item the list already gave, differing only in case, in `file://` form, an empty list, all-whitespace text — plus one run through `Plan`: a folder appearing in both formats must still be treated as "a single folder was dropped", otherwise the fast path silently stops applying. A junction or `icacls` fixture that cannot be created is counted as a **failure**, otherwise the case count quoted here would be one short of what actually ran).

Build and run (this machine has no Windows App Runtime, so it goes self-contained):

```powershell
cd winui
dotnet build winui.csproj -c Debug -p:Platform=x64 -p:RuntimeIdentifier=win-x64 `
  -p:SelfContained=true -p:WindowsPackageType=None -p:WindowsAppSDKSelfContained=true
# start the backend service first, then run the UI
bin\x64\Debug\net8.0-windows10.0.19041.0\win-x64\winui.exe
```

Caveats:

- Verified end to end: fill the source folder → click 开始分类 → the four category cards, the multi-page note, the ticket fields and the summary all render, with the log streaming while the scan runs.
- UI-side fallbacks: `App` installs an `UnhandledException` handler that appends to `%LocalAppData%\invoice_management_system\crash.log` and keeps the window alive; `MainWindow` falls back to 首页 and rewrites `last_page.txt` if restoring the previous page throws, so a bad page cannot lock the app out of its own settings; `WsClient`'s receive loop isolates subscriber callbacks and does its teardown in a `finally`, so a handler exception can no longer kill the loop silently.
- Private bytes measured across 96 page switches ("nav_leak_check.ps1") grow about 19 MB whether or not the frame's back stack is cleared and its page cache is bounded, so the growth is not attributable to those caches; the frame no longer keeps a back stack (the back button is disabled) and caches only the current page, which is the right configuration, but this harness cannot prove a leak was fixed.
- Scan lines are also kept in a process-level copy (capped at 500 lines): navigating away and back rebuilds the page, and the copy is replayed so the returning page is not blank. One generation counter per scan means the POST response and the WebSocket `done` push render only once in a given instance, while an instance created mid-scan still gets its result from the `done` push.
- The HTTP client's `Timeout` is 20 minutes, and that number comes from measurement: scanning 60 real tickets took 5.6 s on this machine (≈94 ms per file), so a full batch at the 2000 cap needs roughly 3 minutes, leaving several multiples of headroom for big multi-page item tables. Even so, expiring does **not** mean the scan failed: the service keeps working and still pushes `done` over `/ws`, which the page renders when it arrives. The log for that branch therefore reads "the response timed out, but the service may still be running — check the log below or the archive folder before clicking again" rather than "local service unresponsive", because the latter makes people re-click and archive the same batch twice (clashes append rather than overwrite, so the result is a duplicate set).
- A full 2000-file batch pushed three UI paths to their limit, all fixed: log lines are no longer appended with `LogBox.Text +=` plus a forced `UpdateLayout()` per line (they queue and flush once per UI turn, with a display cap); `WsClient` no longer calls `Encoding.UTF8.GetString` per 8192-byte frame, which split a Chinese character across frames into two U+FFFD and could make the whole `done` payload unparseable — it now uses a stateful `Decoder`; and a push seen by several live page instances is handled once, keyed on the payload object identity.
- `done` pushes are paired with clicks by ordinal. Scans are serialised server-side so pushes arrive in click order, and a push whose ordinal is lower than the click count is a **late result of an earlier, already-timed-out round**: it appends one log line saying how many were archived instead of taking the newest round's render slot. Without this, clicking again after a timeout made the previous batch fill the cards and the real result got marked "already rendered". Every POST exit except the timeout re-syncs the two counters.
- Found in review and **deliberately not fixed**: clicking 开始分类 twice re-sends the same source paths and the service only appends `(1)(2)` (no content/mtime idempotency), so duplicate tickets can reach the ledger — the only defences are the timeout wording and the ordinal pairing above; `DropPlan.Pdfs/NonPdf/AlreadyArchived` are not recomputed after truncation, so the log shows both "3000 PDFs" and "only 2000 sent"; a very large drop still reads every `StorageItem.Path` on the UI thread (thousands of items means thousands of cross-process property calls). `DragOver` no longer pays that cost per frame — the "can this be dropped here" answer is asked once, in `DragEnter`, cached in `_dragAcceptable` and cleared on leave/drop; if no Enter arrived, `DragOver` asks live **and caches what it got**. Both calls go through one `ProbeDraggable`, because `AvailableFormats`/`Contains` are cross-process and throw when the source releases its data object — and they sit in **synchronous** handlers, so an exception escaping there is exactly the "dragging onto the box does nothing" symptom; the probe degrades to "not acceptable for this drop" and logs at most once per drag (DragOver fires a dozen times a second). the client/service cap equality is asserted by parsing source text, not by behaviour.
- The last source folder is restored into the box (`source_folder`), so it does not have to be re-picked every launch.
- The receiving half of drag and drop (the `files` protocol, non-PDF rejection, multi-page archiving) was verified by sending the exact payload the UI produces straight to the service. **The mouse drag itself still needs one manual test on a real desktop** — automating an OLE drag means writing a script that injects mouse events, and Defender deletes such a file the moment it lands on this machine. A cleaner route was also tried and ruled out: a second process builds a CF_HDROP data object and calls `ole32!DoDragDrop` while its own window is kept glued to the physical cursor so the system's hit test lands on the drop zone (the probe confirmed the point belonged to this window). Without a genuine mouse-button-up event, however, OLE's drag loop **never calls back into the source's `IDropSource`** (`QueryContinueDrag`/`GiveFeedback`: 0 calls each), and posting `WM_LBUTTONUP` to the process's own thread queue does not help either — so the release itself can only be done by hand.
- A third route was tried and died once, recorded so nobody repeats it: delivering the drop **from another process**, following ole32's own lookup - `WindowFromPoint` → `GetParent` chain → `GetProp(hwnd,"OleDropTargetInterface")` → `IDropTarget::DragEnter/DragOver/Drop`. The first half holds (a drop target really is registered at that point); the second half does not: `WindowFromPoint` returns the **topmost** window, so anything overlapping the UI steals the hit, and the pointer `GetProp` hands back is only valid inside the **owning process's address space** - dereferencing it out of process is a `Fatal CLR error (0x80131506)`. ole32 can use it because that lookup happens inside the target process. The third route is closed too: querying the same property **in-process** - all 10 HWNDs of this process (7 top-level plus their children) were scanned and both `OleDropTargetInterface` and `OleDropTarget` were empty, i.e. WinUI 3 does not expose its OLE hookup through that public channel, so Microsoft's half cannot be driven from outside. The probe therefore became something that *can* be verified: `winui\Services\DropSelfTest.cs` (runs only when `INVOICE_DROP_SELFTEST` is set, dead code otherwise) reports the runtime state of **our own** wiring and `temp\drop_transport_check.ps1` (8 checks) asserts `DropZone.AllowDrop=True`, `FolderPathBox.AllowDrop=True`, `PasteButton.IsEnabled=True`, the list starting at 0 items, and that `WindowFromPoint` over the source-box center returns a window whose pid equals this process (measured: `Microsoft.UI.Content.DesktopChildSiteBridge` → `WinUIDesktopWin32WindowClass`). Those are exactly the guards against the regression that really happened once: a page reused from the frame cache leaving `AllowDrop = false`, which looks like "I can't drop anything here". Conclusion unchanged: **the physical mouse-up has to be done by a human**; everything else in the chain now has runtime evidence.
- Two related guards now live in production code: a hung `GetStorageItemsAsync` no longer keeps the deferral open indefinitely - `DropReadTimeout` caps it at 20 seconds and says so ("this source did not produce a file list within 20 seconds; the ticket is probably still downloading or the cloud placeholder has not landed - nothing was added this time") before releasing the deferral; and `ReadDroppedPathsAsync` now tells apart "the source never offered a list" from "the source **claimed** a list but rendered no items", so the second case is no longer misreported as "it only gave a link or a thumbnail, press Ctrl+C first".
- Names ending with a dot (`点结尾.pdf.`) are handled the same way on both sides: Windows treats them as the same file as `点结尾.pdf` (`File.Exists` answers true) while `PurePath.suffix` returns an empty string, so the UI checks the extension through `DropInbox.IsPdf` (trailing dots/spaces stripped) and `classify_invoices` applies the same rule. Explorer cannot even create such a name (it strips the dot), so `temp\dotted_name_probe.py` writes it through the `\\?\` verbatim channel and posts it to the live service - 5 checks: the file really exists, it is not rejected as "not a PDF", it gets classified, the archive count includes it, and it lands in the same category as the dot-free control.
- One end-to-end check drives the live UI against the live service with real ticket samples: `temp\e2e_drop_check.ps1` (6 checks). Samples are copied into a scratch fixture dir first (so archived names carry no original filename, and the assertions echo only counts and category names - ticket contents stay private). It proves: two different invoice types enter the list -> the service reports 「已归档 2 个发票」 -> each type lands in its own category folder -> the archive really gains two copies -> the batch dropped is what got processed rather than the source folder -> **the originals' size and both timestamps are unchanged**. It needs the service's `INVOICE_OUT_DIR` pointed at `temp\service_out` before running and restored afterwards.
- The rendering contract has its own automated check: `temp\stub_service.py` is a fake service that returns a canned `ClassifyReport`, and `temp\render_check.ps1` points the UI at it, triggers one scan and asserts 14 things from the control text read back over UI Automation (per-category counts, the multi-page note, archive path, the ¥ prefix, the warning block and its truncation note, the summary, the non-invoice/unreadable counts, the separate 归档目录里的副本 line, the log echoed with the response, and that each ticket line names the file it came from; the last assertion checks an elevated launch does not falsely warn about 管理员权限 — running the app as administrator is exactly what makes Windows refuse drops from Explorer/WeChat, so the page now says so in its log instead of leaving 「拖了没反应」unexplained. It restores the 8765 address when done.
- `temp\settings_ledger_check.ps1` clicks through the settings page with UI Automation and asserts 5 things: the health reply is shown, the push connection succeeds, filled-in fields report 「已保存」, **clearing the service address reports 「空着的没改」 instead of pretending it was saved**, and a ledger 404 is not reported as a dead service. The connectivity test carries a 5-second timeout so it cannot hang on the client's 20-minute scan timeout.
- Live log now survives leaving the page. The WebSocket handler is attached **once per process** and is no longer unsubscribed in `InvoicePage_Unloaded`: unsubscribing meant zero subscribers while a scan was running and the user sat on another page, and those pushes were lost *at the source* — the service only folds logs into the POST response when it detects no subscriber, and that round had already been dispatched. Handling is now dispatched to the live page (`_active ?? this`), and with no live page the lines still land in the process-level log copy that `ReplayLog` replays on return. A page rebuilt after navigation also re-renders the last report instead of showing empty cards next to a 「已归档 N 个」 log line. Verified by `temp\ws_offpage_check.ps1` (6 checks) against `temp\slow_push_service.py` (one line per second, six lines, long enough to switch pages mid-scan): the four lines pushed while the page was away all arrive, each exactly once, the page really was off-screen, and the cards match the log after returning.
- De-duplication of the drop list went from pairwise to keyed. `DropInbox.Key` (the `Normalize` form) in a `HashSet` is used everywhere — loose files pass through `Distinct` first, enumerated files, text fallback and the UI accumulate loop are all O(1) lookups. Previously every comparison ran two `GetFullPath` calls inside a linear scan, so dragging two thousand PDFs out of a folder pinned the UI thread for seconds and looked like "the drop did nothing". The later raw-string linear pass was dropped too: it treated `C:/a.pdf` and `C:\a.pdf` as two tickets and was pure re-work.
- Dropping a folder means "choose this directory", and that self-write is no longer mistaken for a user edit. `TextChanged` arrives one beat late, so the old synchronous flag was already false by then: the write bumped the drop sequence for nothing, which invalidates a drop running concurrently. The guard now remembers the *value* (`_programmaticFolder`) and digests the event when the box text equals what the UI just wrote. That branch also stores the folder as the last source directory, so it survives a restart instead of an empty box.
- On a non-2xx reply `ApiClient` throws `ApiStatusException` (carrying the status code), which lets the ledger page tell "endpoint not implemented yet (404/501)" apart from "local service is down" instead of reporting a 404 as a dead service.
- **Running the plain `dotnet build` output without those flags crashes** with `0x80040154 REGDB_E_CLASSNOTREG` — the Windows App Runtime isn't installed; either build self-contained as above or install `Microsoft.WindowsAppRuntime`.
- `dotnet` on PATH here is `C:\Program Files (x86)\dotnet`, which has no SDK — use `C:\Program Files\dotnet\dotnet.exe` (or VS with the .NET desktop workload).
- With `HTTP_PROXY/HTTPS_PROXY` pointing at a dead local proxy, `dotnet restore` fails with NU1301 SSL errors; clearing those two variables restores direct access to nuget.org (VS uses the system proxy instead, not these variables).
- The ledger page and the 首页 stat cards are placeholders until the SQLite schema (need.md 五) is implemented.
