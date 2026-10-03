# invoice_management_system

> ## 开发中，功能尚不完整
>
> 目前只实现了：火车票票面字段提取、电子发票类型与开票日期/发票号码提取、按票种收集 PDF。
> 其他票种的字段提取、逐页解析、与报销台账的对账都还没有落地。
> 函数签名和返回的 dict 结构都可能随开发变动，引用前请先看代码确认。

[English documentation](README.en.md)

用 pdfplumber 从发票 PDF 中读出票面关键字段。**仅支持 Windows**，未在其他系统验证过。
- **提取函数只处理单页**：多页 PDF 在收集器里按第一页判类，副本落到所属类别下的「多页文件」子文件夹，逐页解析尚未实现。

## 二、环境配置（uv）

### 1. 安装 uv

在 PowerShell 里直接运行：

```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

也可以走 winget（本项目用 0.12.x），注意版本：

```powershell
winget install --id=astral-sh.uv -e
```

用 `uv --version` 确认uv版本（本项目使用 uv 0.12.x）。

### 2. 初始化本模块环境

```powershell
cd invoice_recognition
uv sync
```

`uv sync` 读取 `pyproject.toml` + `uv.lock`，自动完成：

- 本机没有满足 `>=3.12` 的解释器时由 uv 下载一个
- 按锁文件安装依赖：`pdfplumber 0.11.10`（连带 `pdfminer-six`、`pypdfium2`、`pillow`、`charset-normalizer`、`cryptography`）。

---

## 三、提取脚本

提取函数共用同一套流程：`page.extract_text()` 取整页文字 → `unicodedata.normalize("NFKC", text)` 归一化 → 逐行正则匹配字段，版式固定且文字粘连的区域改用 `page.crop(box)` 单独取词。

归一化：PDF 抽出的汉字常是部首兼容字（如 `⼦` U+2FBA），不还原成 `子` U+5B50，正则一定匹配不上。

### `train_invoice_recognition.py`

`extract_invoice_train(page)`：提取火车票（电子客票）票面字段。入参为 `pdfplumber.Page`，返回 `dict`。

| 键 | 含义 | 备注 |
|---|---|---|
| `is_train` | 是否为火车票 | 在标题裁剪框 `(89, 8, (418 + page.width) / 2, 38)` 内匹配「电子发票（铁路电子客票）」；为 `False` 时返回的 dict **只含这一个键**，调用方必须先判此键再分流 |
| `e_ticket_number` | 电子客票号 | 「电子客票号」后紧跟的 25 位 |
| `state` | 客票状态 | 紧跟客票号的文字，如 `退票`／`补票`／`始发改签`；没有则为 `None` |
| `invoice_no` | 发票号码 | 8–20 位，兼容 `发票号码` 与 `No.` 两种前缀 |
| `invoice_date` / `invoice_time` | 行程日期 / 发车时间 | 如 `2026年04月17日` + `11:54` |
| `total_amount` | 价税合计（小写） | 如 `¥39.00` |
| `id` / `person` | 脱敏身份证号 / 乘车人姓名 | 身份证形如 `1234567890****123X` |
| `site` | `[起点站, 终点站]` | 由两个固定裁剪框各取首行；坐标按手头样本票量得，票样版式变了要重新测 |

注意事项：

- 三条取文路径并存：`is_train` 用标题裁剪框，`e_ticket_number`/`state` 用 `extract_text(use_text_flow=True)` 的流式文本，其余字段用默认阅读顺序的文本；都要先 NFKC 归一化。
- 日期时间提取失败时函数**提前返回半成品 dict**，其后的键可能缺失或为 `None`，不能假设键齐全。
- 失败提示走 `tkinter.messagebox.showwarning`，未来构建 GUI 时再换成日志。

### `regular_invoice_recognition.py`

`extract_invoice_regular(page)`：判断是否电子发票（普通发票），是则再取开票日期和发票号码。返回 `dict`。

| 键 | 含义 | 备注 |
|---|---|---|
| `is_regular` | 是否普通发票 | 在标题裁剪框 `(128, 10, 416, 49)` 内匹配「电子发票（普通发票）」，括号全角半角都兼容；为 `False` 时**只返回这一个键** |
| `invoice_date` | 开票日期 | 票头裁剪框 `(432, 17, page.width, 69)` 内匹配 `开票日期：2026年9月2日` |
| `invoice_number` | 发票号码 | 同一裁剪框内匹配 20 位数字 |

注意事项：

- `is_regular` 为 `False` 时不会走到日期/号码那一步，调用方需先判此键。
- 日期或号码没匹配到时对应值为 `None` 并弹警告，不抛异常。
- 标题措辞变化或新增票种都要扩规则。

### `VAT_invoice_recognition.py`

`extract_invoice_VAT(page)`：判断是否电子发票（增值税专用发票）。返回 `dict`。

| 键 | 含义 | 备注 |
|---|---|---|
| `is_vat` | 是否增值税专用发票 | 在标题裁剪框 `(121, 14, 432, 52)` 内匹配「电子发票（增值税专用发票）」；为 `False` 时**只返回这一个键** |

注意事项：

- **目前只有关键字判定**，函数体内 `# 2.` 之后的票面字段（日期、号码等）还未实现。
- 原来的三合一 `type` 判据已拆成 `is_regular` / `is_vat` 两个模块，`normal_invoice_recognition.py`（「电子发票」字样筛查）也已删除，那道筛查现在内置在 `classify_invoices.py` 的 `ELECTRONIC_INVOICE_RE`。

### `classify_invoices.py`

收集器：选一个文件夹后递归扫描其中的 PDF，按第一页判定票种，把**副本**（原件不动）复制到 `invoice_recognition/temp/发票分类/` 下。目录结构：

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

分类顺序：先用 `ELECTRONIC_INVOICE_RE`（整页逐行匹配「电子发票」字样）作总开关筛掉无关 PDF，再依次问 `is_train` → 火车票、`is_vat` → 增值税发票、`is_regular` → 普通发票，过筛但三种都不匹配的归其他发票。没过筛的文件不复制，只在日志里列出。多页 PDF 归到所属类别的 `多页文件` 子文件夹；重名文件自动追加 `(1)(2)`。

注意事项：

- 扫描跑在后台线程里，日志经队列回投；主线程被逐个 PDF 阻塞时窗口拖动会变成「未响应」。
- 批量扫描时提取函数里的弹窗会被临时屏蔽（否则每票一个对话框卡住流程，且后台线程不能调 Tk），异常改由日志列出。
- 打不开的 PDF 归入「无法解析」，同样不复制。
- pdfminer 对缺 `FontBBox` 的字体逐条告警（`Could not get FontBBox …`），实测不影响文字抽取，只有一条 `_QuietFontBBox` 过滤器消掉这类噪音。
