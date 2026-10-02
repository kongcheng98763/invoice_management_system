# invoice_management_system

> ## 开发中，功能尚不完整
>
> 目前只实现了：火车票票面字段提取、发票类型判定。其他票种的字段提取、多页 PDF、归档与对账都还没有落地。
> 函数签名和返回的 dict 结构都可能随开发变动，引用前请先看代码确认。

[English documentation](README.en.md)

用 pdfplumber 从发票 PDF 中读出票面关键字段。**仅支持 Windows**，未在其他系统验证过。
- **只处理单页 PDF**：`__main__` 检测到页数大于 1 就弹警告退出，多页文件的逐页归属尚未实现。

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

两个脚本共用同一套流程：`page.extract_text()` 取整页文字 → `unicodedata.normalize("NFKC", text)` 归一化 → 逐行正则匹配字段，版式固定且文字粘连的区域改用 `page.crop(box)` 单独取词。

归一化：PDF 抽出的汉字常是部首兼容字（如 `⼦` U+2FBA），不还原成 `子` U+5B50，正则一定匹配不上。

### `train_invoice_recognition.py`

`extract_invoice_train(page)`：提取火车票（电子客票）票面字段。入参为 `pdfplumber.Page`，返回 `dict`。

| 键 | 含义 | 备注 |
|---|---|---|
| `is_train` | 是否为火车票 | 以票面上有无「电子客票号」为判据；为 `False` 时返回的 dict **只含这一个键**，调用方必须先判此键再分流 |
| `E-ticket_number` | 电子客票号 | 25 位 |
| `invoice_no` | 发票号码 | 8–20 位，兼容 `发票号码` 与 `No.` 两种前缀 |
| `invoice_date` / `invoice_time` | 行程日期 / 发车时间 | 如 `2026年04月17日` + `11:54` |
| `total_amount` | 价税合计（小写） | 如 `¥39.00` |
| `id` / `person` | 脱敏身份证号 / 乘车人姓名 | 身份证形如 `1234567890****123X` |
| `site` | `[起点站, 终点站]` | 由两个固定裁剪框各取首行；坐标按手头样本票量得，票样版式变了要重新测 |

注意事项：

- 日期时间或站点提取失败时函数**提前返回半成品 dict**，其后的键可能缺失或为 `None`，不能假设键齐全。
- 失败提示走 `tkinter.messagebox.showwarning`，未来会构建 GUI 的场景并换成日志。

### `Regular_invoice_recognition.py`

`extract_invoice_normal(page)`：判断发票类型。入参同样是 `pdfplumber.Page`，返回 `dict`。

| 键 | 含义 | 备注 |
|---|---|---|
| `type` | 发票类型 | `"regular_invoice"` = 电子发票（普通发票）；`"vat_special_invoice"` = 电子发票（增值税专用发票）；`None` = 未匹配到已知类型 |

注意事项：

- **目前只实现了类型判定**，开票日期、发票号码等字段还未完成。
- 判定靠匹配票面标题「电子发票（普通发票）/（增值税专用发票）」，括号做了全角半角兼容；标题措辞变化或新增票种都要扩规则。
- 未匹配到类型时返回 `{"type": None}` 并弹警告，调用方需先判 `type`。
