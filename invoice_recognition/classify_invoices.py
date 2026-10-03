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

# 提取函数失败时会弹 tkinter 窗：批量扫描既会被逐个对话框卡住，也不能在后台线程里碰 Tk，
# 因此先保存 GUI 自己要用的 showwarning，再把模块级的换成空操作
_gui_warn = messagebox.showwarning
messagebox.showwarning = lambda *args, **kwargs: None

from VAT_invoice_recognition import extract_invoice_VAT
from regular_invoice_recognition import extract_invoice_regular
from train_invoice_recognition import extract_invoice_train

CATEGORIES = ["火车票", "增值税发票", "普通发票", "其他发票"]
MULTI_PAGE_FOLDER = "多页文件"
DEFAULT_OUT = Path(__file__).resolve().parent / "temp" / "发票分类"

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


def find_pdfs(search_root, out_dir):
    for path in sorted(search_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        if out_dir in path.parents:          # 不把自己刚复制出去的副本再扫一遍
            continue
        if ".venv" in path.parts:
            continue
        yield path


def collect(search_root, out_dir, report):
    """把判定为电子发票的 PDF 复制归档，返回 (各类数量, 其中多页数, 非电子发票列表, 无法解析列表)。"""
    for name in CATEGORIES:
        (out_dir / name).mkdir(parents=True, exist_ok=True)
        (out_dir / name / MULTI_PAGE_FOLDER).mkdir(parents=True, exist_ok=True)

    counts = {name: 0 for name in CATEGORIES}
    multi_page = 0
    non_invoices = []
    unreadable = []

    for pdf in find_pdfs(search_root, out_dir):
        rel = pdf.relative_to(search_root)
        try:
            with pdfplumber.open(pdf) as doc:
                if not doc.pages:
                    raise ValueError("PDF 无页面")
                # 提取函数都是单页设计，多页文件一律按第一页判定票种
                category = classify(doc.pages[0])
                is_multi = len(doc.pages) > 1
        except Exception as exc:
            unreadable.append((rel, str(exc)))
            report(f"[无法解析，未归档] {rel} -> {exc}")
            continue

        if category is None:
            non_invoices.append(rel)
            report(f"[非电子发票，未归档] {rel}")
            continue

        folder = out_dir / category / MULTI_PAGE_FOLDER if is_multi else out_dir / category
        shutil.copy2(pdf, unique_path(folder, pdf.name))
        counts[category] += 1
        if is_multi:
            multi_page += 1
        report(f"[{category}{'/多页' if is_multi else ''}] {rel}")

    return counts, multi_page, non_invoices, unreadable


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

        self.log = scrolledtext.ScrolledText(self, state="disabled", wrap="none")
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def pick_folder(self):
        chosen = filedialog.askdirectory(title="选择要搜索 PDF 的文件夹")
        if chosen:
            self.folder_var.set(str(Path(chosen).resolve()))

    def write(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def run(self):
        if self.scanning:
            return
        folder = self.folder_var.get().strip().strip('"')
        search_root = Path(folder).resolve() if folder else None
        if search_root is None or not search_root.is_dir():
            _gui_warn("提示", "请先选择存在的文件夹")
            return

        # 逐个 PDF 解析要几秒到几分钟，放在主线程里 Tk 不处理事件，拖动窗口就会变成「未响应」；
        # 因此扫描进后台线程，只通过队列往回传文本，控件一律由主线程的 _poll 更新
        self.scanning = True
        self.run_btn.configure(state="disabled", text="分类中…")
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
        counts, multi_page, non_invoices, unreadable = result
        self.write(f"\n已归档 {sum(counts.values())} 个发票（其中多页 {multi_page} 个）到 {DEFAULT_OUT}")
        for name in CATEGORIES:
            self.write(f"  {name}: {counts[name]}")
        self.write(f"  非发票（未复制）: {len(non_invoices)}")
        self.write(f"  无法解析（未复制）: {len(unreadable)}")

    def _finish_scan(self):
        self.scanning = False
        self.run_btn.configure(state="normal", text="开始分类")

    def open_out(self):
        DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
        os.startfile(DEFAULT_OUT)


if __name__ == "__main__":
    App().mainloop()
