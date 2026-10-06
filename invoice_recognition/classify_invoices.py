"""扫描所选文件夹下的全部 PDF，把电子发票按票种复制到 temp/发票分类/ 下的四个文件夹。

用法（在 invoice_recognition 目录下）：
    python classify_invoices.py        # 打开窗口，选择要搜索的文件夹后点「开始分类」

类别为 火车票 / 增值税发票 / 普通发票 / 其他发票，判定只看第一页（提取函数均为单页设计）。
先用「电子发票」字样筛掉非发票文件，再细分票种；多页 PDF 在所属类别下再放一层
「多页文件」子文件夹，单页 PDF 直接放在类别文件夹里。
不是电子发票的 PDF 不复制，只在日志里列出。
"""

import logging
import os
import re
import shutil
import threading
import unicodedata
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, scrolledtext

import pdfplumber
import tkinter as tk


class _QuietFontBBox(logging.Filter):
    """pdfminer 碰到字体没有 FontBBox 时逐条告警（包围盒退化成 0,0,0,0，文字照样能抽出来）。

    批量扫描时这类噪音会淹没结果，只挡这一条，其余警告照常输出。
    """

    def filter(self, record):
        return "Could not get FontBBox" not in record.getMessage()


logging.getLogger("pdfminer.pdffont").addFilter(_QuietFontBBox())

# 提取函数的告警只攒清单不弹窗（批量扫描会被逐个对话框卡死），
# 要给用户看的地方自己取：tkinter 窗口在扫描收尾时取出来列一遍并弹一次汇总，
# FastAPI 那边把这一轮的告警放进响应的 warnings 字段
from VAT_invoice_recognition import extract_invoice_VAT, extract_invoice_VAT_pages
from regular_invoice_recognition import extract_invoice_regular, extract_invoice_regular_pages
from train_invoice_recognition import extract_invoice_train
from invoice_item_recognition import take_warnings

CATEGORIES = ["火车票", "增值税发票", "普通发票", "其他发票"]
MULTI_PAGE_FOLDER = "多页文件"
DEFAULT_OUT = Path(__file__).resolve().parent / "temp" / "发票分类"
_COPY_LOCK = threading.Lock()

# normal_invoice_recognition 已删除，「电子发票」总开关留在这里：
# 三种票的标题都以「电子发票」开头，用它先筛掉说明书、采购单之类无关 PDF，省下后面的裁剪解析
ELECTRONIC_INVOICE_RE = re.compile(r"\s*电\s*子\s*发\s*票")


def is_electronic_invoice(page):
    for line in (page.extract_text() or "").splitlines():
        if ELECTRONIC_INVOICE_RE.search(unicodedata.normalize("NFKC", line)):
            return True
    return False


def classify(page):
    """把一页 PDF 归到四类之一；不是电子发票返回 None（不归档）。

    先总开关后细分：三个提取函数各自裁剪标题区域匹配「电子发票（…）」全称，
    覆盖面窄，直接逐个调用会把大量无关 PDF 也解析一遍。
    """
    if not is_electronic_invoice(page):
        return None
    if extract_invoice_train(page).get("is_train"):
        return "火车票"
    if extract_invoice_VAT(page).get("is_vat"):
        return "增值税发票"
    if extract_invoice_regular(page).get("is_regular"):
        return "普通发票"
    return "其他发票"


def unique_path(folder, filename):
    """目标重名时追加 (1)(2)…，避免不同目录的同名 PDF 互相覆盖。"""
    target = folder / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    i = 1
    while (folder / f"{stem}({i}){suffix}").exists():
        i += 1
    return folder / f"{stem}({i}){suffix}"


def is_directory_link(entry):
    """这个目录项是不是「指向别处的目录」——junction 或目录软链。

    Windows 上 os.scandir 把 junction 当普通目录（os.path.islink 也说 False），
    所以不单独问 is_junction 的话，自指向的链接照样被一路走进去。
    """
    try:
        if entry.is_junction():
            return True
    except (AttributeError, OSError):
        pass                    # 3.12 以前没有 is_junction
    try:
        return entry.is_symlink() and entry.is_dir()
    except OSError:
        return True


def walk_files(root, on_error=None):
    """自己走目录树，不走进目录链接。

    rglob 会顺着 junction / 目录软链绕回去：一个自指向的链接能把同一个文件数上几十遍
    （实测 1 个 PDF 收成 32 条），既白跑解析又往归档里堆副本。文件的软链照旧跟。

    on_error(dir, exc) 用来把「读不动的目录」报出去：以前遇到就静默 continue，
    结果一个 ACL 拒读的目录扫完只说「归档 0 个」，看起来像里面没有发票。
    """
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        # entry.path 继承 current 的写法，可 current 短而这一条目刚好跨过门槛时它还是普通写法：
                        # 不在这里换写法，深下去的那一层要么 scandir 失败（整棵子树被当成「读不动」跳过），
                        # 要么交出去一条读不到的路径，明明是 PDF 却被报成「不是可读取的普通文件」。
                        # 目录和文件的门槛不同（见 fs_path），所以这里先问一次 is_dir 再决定换不换
                        is_dir = entry.is_dir(follow_symlinks=False)
                        path = fs_path(entry.path, is_dir=is_dir)
                        if is_dir and not is_directory_link(entry):
                            pending.append(path)
                        elif not is_dir:
                            yield path
                    except OSError:
                        continue      # 没权限读这个条目就跳过
        except OSError as ex:
            # 整个目录读不动（网络盘断了、没权限），跳过它而不是中断扫描
            if on_error is not None:
                on_error(current, ex)


def find_pdfs(search_root, out_dir, on_error=None):
    for path in sorted(Path(p) for p in walk_files(search_root, on_error)):
        if not Path(str(path)).name.rstrip(". ").lower().endswith(".pdf"):
            # Windows 认为「票.pdf.」和「票.pdf」是同一个文件（结尾的点会被剥掉），
            # 而 PurePath.suffix 对前者答空串——照 suffix 判会把一张真票拒成「不是 PDF」
            continue
        # 归档目录（服务侧可用 INVOICE_OUT_DIR 另指一处）里的副本不能再被扫第二遍。
        # 比较要用「摘掉 \\?\ 前缀」的那一份：整目录扫描的根可能已被 fs_path 换过写法，
        # 带前缀的路径和普通写法的 out_dir 永远比不中，归档里的副本就会被扫第二遍
        plain = Path(strip_verbatim(path))
        if out_dir in plain.parents or DEFAULT_OUT in plain.parents:
            continue
        if ".venv" in plain.parts:
            continue
        yield path


def invoice_fields(doc, category):
    """按类别把整份 PDF 的票面字段解析出来；多页的走逐页解析加跨页拼接。

    records 是单页解析的中间结果（里面装着 InvoiceItem 对象），不跨进程传，去掉。
    """
    if category == "火车票":
        fields = extract_invoice_train(doc.pages[0])
    elif category == "增值税发票":
        fields = extract_invoice_VAT_pages(doc.pages)
    elif category == "普通发票":
        fields = extract_invoice_regular_pages(doc.pages)
    else:
        fields = extract_invoice_regular(doc.pages[0])
    fields.pop("records", None)
    return fields


def _is_archive_copy(path, out_dir):
    """这个文件是不是本来就躺在归档目录（或默认归档目录）里。

    副本不该再被归档一次：拖放那条路是照给定路径处理的，绕开了 find_pdfs 的目录排除，
    少了这道判断的话，把归档目录连着别的文件一起拖进来，每拖一次副本就翻一倍。
    比较之前要先摘掉 `\\?\` 前缀——resolve() 会把它留着，于是 `folder in resolved.parents`
    永远不成立，一份躺在归档目录里的长路径副本就被再归档一次（实测过这个坑）。
    """
    try:
        resolved = Path(strip_verbatim(path.resolve()))
    except OSError:
        return False
    return any(folder in resolved.parents for folder in (out_dir, DEFAULT_OUT))


MAX_PATH = 260                                  # Win32 传统路径上限
DIR_MARGIN = 10                                 # 目录要比文件早一档换写法，见 fs_path
VERBATIM = "\\\\?\\"                            # 原样通道前缀：不规范化、不做 260 检查


def fs_path(raw, is_dir=False):
    r"""把「外部给来的路径」翻译成 Python 这一侧真能打开的那一种。

    超过 260 字符的路径只有走 `\\?\` 原样通道才读得到。本机（系统 LongPathsEnabled=0）实测：
    `os.path.exists` 对一条 420 字符的普通写法答 `False`，而界面那侧的 .NET 不受这个限制
    （System.IO 自己会加前缀）。不接这一步的话就是「界面收下了、服务读不到」，
    一张真发票被报成「不是可读取的普通文件」，用户完全看不出是长度卡的。

    原样通道不做任何规范化，所以先 normpath；UNC 要写成 `\\?\UNC\server\share` 那种形式；
    短路径一律原样返回，不改平时行为。相对路径不加前缀（前缀只吃绝对路径），
    让它继续按「读不到就归无法解析」处理。

    两个门槛不是一个数：
    - 文件是 260：实测普通写法 259 还能 `os.stat`，整 260 就读不到（别按直觉留余量）。
    - 目录是 260-10=250：`os.scandir` 要拿「目录\\*」去做首次匹配，比 `os.path.exists` 更早失败。
      实测一条 256 字符的目录 `os.path.exists` 答 True、`os.scandir` 却报「系统找不到指定的路径」，
      228 字符的目录才 scandir 得动。所以目录这一档取 250，落在实测的 228（通）与 256（不通）之间，
      宁可提前换写法，也不要扫到一半才发现整棵子树没了。
    """
    text = os.path.normpath(str(raw))
    limit = MAX_PATH - DIR_MARGIN if is_dir else MAX_PATH
    if len(text) < limit or text.startswith(VERBATIM):
        return Path(raw)
    if text.startswith("\\\\"):                             # UNC：\\server\share → \\?\UNC\server\share
        return Path(VERBATIM + "UNC\\" + text[2:])
    # 盘符后面必须是分隔符才算绝对路径：`C:foo` 那种「盘符相对」加了前缀反而变成非法写法
    if len(text) >= 3 and text[1] == ":" and text[0].isalpha() and text[2] == "\\":
        return Path(VERBATIM + text)
    return Path(raw)


UNC_VERBATIM = VERBATIM + "UNC\\"               # \\?\UNC\server\share 形式


def strip_verbatim(path):
    r"""把 fs_path 加上的 `\\?\` 前缀摘掉，只用于「给人看」和「比路径」两种场合。

    这串前缀是我们自己选的读法，不是路径本来的样子：
    - 它出现在日志里，界面 LogBox 就冒出一串 `\\?\C:\…`，使用者会以为路径写坏了；
    - 更麻烦的是 resolve() 会保留前缀，拿它和（普通写法的）归档目录比较永远不相等，
      「归档目录里的副本不再归档」这道判断就形同没有。
    """
    text = str(path)
    if text.startswith(UNC_VERBATIM):
        return "\\\\" + text[len(UNC_VERBATIM):]
    if text.startswith(VERBATIM):
        return text[len(VERBATIM):]
    return text


def shown(value):
    """给日志/明细用的一份文本：异常消息整串可能带着前缀，摘掉再往外发。"""
    return strip_verbatim(str(value))


def collect(search_root, out_dir, report, with_fields=False, files=None):
    """把判定为电子发票的 PDF 复制归档，返回 (各类数量, 其中多页数, 非电子发票列表, 无法解析列表, 扫描明细)。

    扫描明细每项是 {file, category, is_multi_page, note}，未归档的文件 category 为空、原因写在 note；
    with_fields 为 True 时每项再多带一份票面字段 fields —— 服务侧要显示给界面，
    tkinter 那边扫描量大，按原样只分类不解析。
    files 给了就只处理这批路径（界面从微信或资源管理器拖文件进来的场景），否则递归扫 search_root。
    """
    for name in CATEGORIES:
        (out_dir / name).mkdir(parents=True, exist_ok=True)
        (out_dir / name / MULTI_PAGE_FOLDER).mkdir(parents=True, exist_ok=True)

    counts = {name: 0 for name in CATEGORIES}
    multi_page = 0
    non_invoices = []
    unreadable = []
    entries = []

    # 空清单等同于「没指定文件」，退回整目录扫描：界面没拖东西时发的就是 files: []
    blocked_dirs = []

    def note_blocked(directory, ex):
        blocked_dirs.append((directory, getattr(ex, "strerror", None) or str(ex)))

    if files:
        # 路径可能长到 Python 读不动（见 fs_path），显示用的 rel 仍按普通写法取文件名
        sources = [(fs_path(f), Path(f).name) for f in files]
    else:
        # walk_files 现在交出来的路径已经逐条换过写法（见那里），这里只负责把「显示用的相对路径」
        # 从同一根上摘下来：relative_to 两边得是同一种写法，所以两边都先去掉 \\?\ 前缀。
        # 根目录很短、里面某条路径却超过 260 时，界面上看到的仍是普通写法，日志里不会冒出 \\?\
        root = fs_path(search_root, is_dir=True)
        root_plain = strip_verbatim(root)
        sources = [(p, Path(strip_verbatim(p)).relative_to(root_plain))
                   for p in find_pdfs(root, out_dir, note_blocked)]

    # 先说「哪些目录根本没读进去」，否则扫完 0 个文件时界面只剩一句「已归档 0 个」，
    # 用户会把「读不动」当成「这个文件夹里没有发票」
    for directory, why in blocked_dirs[:5]:
        report(f"[目录读不动，已跳过] {strip_verbatim(directory)}：{why}")
    if len(blocked_dirs) > 5:
        report(f"[目录读不动] 另有 {len(blocked_dirs) - 5} 个目录没读，只列前 5 个")

    for pdf, rel in sources:
        if not pdf.name.rstrip(". ").lower().endswith(".pdf"):
            non_invoices.append(rel)
            entries.append({"file": str(rel), "category": None, "is_multi_page": False, "note": "不是 PDF"})
            report(f"[不是 PDF，未归档] {rel}")
            continue
        # 从归档目录里拖出来的副本不再归档：否则每拖一次归档就翻一倍
        if _is_archive_copy(pdf, out_dir):
            non_invoices.append(rel)
            entries.append({"file": str(rel), "category": None, "is_multi_page": False, "note": "副本已在归档目录里，不重复归档"})
            report(f"[跳过副本] {rel}")
            continue
        # 命名管道、设备名这类看着像 .pdf 的路径会在 open 里长期阻塞，卡住整个扫描，先挡掉
        if not pdf.is_file():
            unreadable.append((rel, "不是可读取的普通文件"))
            entries.append({"file": str(rel), "category": None, "is_multi_page": False, "note": "不是可读取的普通文件"})
            report(f"[不是普通文件，未归档] {rel}")
            continue
        try:
            with pdfplumber.open(pdf) as doc:
                if not doc.pages:
                    raise ValueError("PDF 无页面")
                # 提取函数都是单页设计，多页文件一律按第一页判定票种
                category = classify(doc.pages[0])
                is_multi = len(doc.pages) > 1
                fields = invoice_fields(doc, category) if (category and with_fields) else None
        except Exception as exc:
            # 异常消息里带的是我们换过写法的那条路径，摘掉前缀再往外发（见 shown）
            why = shown(exc)
            unreadable.append((rel, why))
            entries.append({"file": str(rel), "category": None, "is_multi_page": False, "note": f"无法解析：{why}"})
            report(f"[无法解析，未归档] {rel} -> {why}")
            continue

        if category is None:
            non_invoices.append(rel)
            entries.append({"file": str(rel), "category": None, "is_multi_page": False, "note": "非电子发票"})
            report(f"[非电子发票，未归档] {rel}")
            continue

        target_dir = out_dir / category / MULTI_PAGE_FOLDER if is_multi else out_dir / category
        # 归档那一侧也要换写法：INVOICE_OUT_DIR 指到很深的位置时（或票名本身就长），
        # 目标路径过了 260 会让 unique_path 的存在检查永远答「不存在」，
        # 于是不但加不上 (1)，还会把已有副本覆盖掉——那是「只复制、不动旧副本」的硬约束
        folder = fs_path(target_dir, is_dir=True)
        # 重名要「先查再copy」，两个扫描同时跑会撞在同一个 (1) 上
        # copy 单独兜异常：一个文件复制失败（被占用、目标名过长）不该把整轮结果一起丢掉
        try:
            with _COPY_LOCK:
                shutil.copy2(pdf, unique_path(folder, pdf.name))
        except Exception as exc:
            why = shown(exc)
            unreadable.append((rel, why))
            entries.append({"file": str(rel), "category": None, "is_multi_page": False, "note": f"复制失败：{why}"})
            report(f"[复制失败，未归档] {rel} -> {why}")
            continue
        counts[category] += 1
        if is_multi:
            multi_page += 1
        entries.append({"file": str(rel), "category": category, "is_multi_page": is_multi,
                        "note": "多页文件" if is_multi else "", "fields": fields})
        report(f"[{category}{'/多页' if is_multi else ''}] {rel}")

    return counts, multi_page, non_invoices, unreadable, entries


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("发票收集与分类")
        self.geometry("720x440")
        self.msgs = Queue()
        self.scanning = False

        row = tk.Frame(self, padx=10, pady=10)
        row.pack(fill="x")
        tk.Label(row, text="搜索目录").pack(side="left")
        self.folder_var = tk.StringVar()
        tk.Entry(row, textvariable=self.folder_var).pack(side="left", fill="x", expand=True, padx=8)
        tk.Button(row, text="选择文件夹", command=self.pick_folder).pack(side="left")

        tk.Label(self, anchor="w", padx=10, text=f"归档目录：{DEFAULT_OUT}").pack(fill="x")

        actions = tk.Frame(self, padx=10, pady=8)
        actions.pack(fill="x")
        self.run_btn = tk.Button(actions, text="开始分类", command=self.run)
        self.run_btn.pack(side="left")
        tk.Button(actions, text="打开结果目录", command=self.open_out).pack(side="left", padx=8)

        self.log = scrolledtext.ScrolledText(self, wrap="none")
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        # 只读靠屏蔽输入事件，而不是 state="disabled"：被禁用的 Text 会连鼠标事件一起吞掉
        # （disabledaction 默认 ignore），滚动条拖动和选中复制都点不动
        self.log.bind("<Key>", self._readonly_key)
        for seq in ("<<Paste>>", "<<PasteSelection>>"):
            self.log.bind(seq, lambda event: "break")

    @staticmethod
    def _readonly_key(event):
        # Ctrl-C（\x03）复制、Ctrl-A（\x01）全选要放行，其余按键吞掉，日志内容不可被改动
        if event.char in ("\x03", "\x01"):
            return None
        return "break"

    def pick_folder(self):
        chosen = filedialog.askdirectory(title="选择要搜索 PDF 的文件夹")
        if chosen:
            self.folder_var.set(str(Path(chosen).resolve()))

    def write(self, line):
        # 用户把视图往上拖走后不再强行拉回底部，否则每来一行就把拖动结果顶掉
        following = self.log.yview()[1] >= 0.999
        self.log.insert("end", line + "\n")
        if following:
            self.log.see("end")

    def run(self):
        if self.scanning:
            return
        folder = self.folder_var.get().strip().strip('"')
        search_root = Path(folder).resolve() if folder else None
        if search_root is None or not search_root.is_dir():
            messagebox.showwarning("提示", "请先选择存在的文件夹")
            return

        # 逐个 PDF 解析要几秒到几分钟，放在主线程里 Tk 不处理事件，拖动窗口就会变成「未响应」；
        # 因此扫描进后台线程，只通过队列往回传文本，控件一律由主线程的 _poll 更新
        self.scanning = True
        self.run_btn.configure(state="disabled", text="分类中…")
        take_warnings()          # 丢掉上一轮（或模块自测）残留的告警
        self.write(f"开始扫描：{search_root}")
        threading.Thread(target=self._scan, args=(search_root,), daemon=True).start()
        self.after(100, self._poll)

    def _scan(self, search_root):
        try:
            result = collect(search_root, DEFAULT_OUT, lambda line: self.msgs.put(("line", line)))
        except Exception as exc:
            self.msgs.put(("error", str(exc)))
        else:
            self.msgs.put(("done", result))

    def _poll(self):
        while True:
            try:
                kind, payload = self.msgs.get_nowait()
            except Empty:
                break
            if kind == "line":
                self.write(payload)
            elif kind == "error":
                self.write(f"扫描中断：{payload}")
                self._finish_scan()
            elif kind == "done":
                self._summarise(payload)
                self._finish_scan()
        if self.scanning:
            self.after(100, self._poll)

    def _summarise(self, result):
        counts, multi_page, non_invoices, unreadable, _entries = result
        self.write(f"\n已归档 {sum(counts.values())} 个发票（其中多页 {multi_page} 个）到 {DEFAULT_OUT}")
        for name in CATEGORIES:
            self.write(f"  {name}: {counts[name]}")
        self.write(f"  非发票（未复制）: {len(non_invoices)}")
        self.write(f"  无法解析（未复制）: {len(unreadable)}")
        # 提取函数在后台线程里只攒告警不弹窗（弹窗会把线程卡住），扫描结束统一在这里报给用户
        warnings = take_warnings()
        if warnings:
            self.write(f"  解析告警 {len(warnings)} 条：")
            for one in warnings[:20]:
                self.write(f"    ! {one}")
            if len(warnings) > 20:
                self.write(f"    …其余 {len(warnings) - 20} 条已省略")
            messagebox.showwarning("解析告警", f"共 {len(warnings)} 条票面解析告警，详见日志")

    def _finish_scan(self):
        self.scanning = False
        self.run_btn.configure(state="normal", text="开始分类")

    def open_out(self):
        DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
        os.startfile(DEFAULT_OUT)


if __name__ == "__main__":
    App().mainloop()
