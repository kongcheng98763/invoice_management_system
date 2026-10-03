"""扫描所选文件夹下的全部 PDF，把电子发票按票种复制到 temp/发票分类/ 下的四个文件夹。

用法（在 invoice_recognition 目录下）：
    python classify_invoices.py        # 打开窗口，选择要搜索的文件夹后点「开始分类」

类别为 火车票 / 增值税发票 / 普通发票 / 其他发票，判定只看第一页（提取函数均为单页设计）。
多页 PDF 在所属类别文件夹下再放一层「多页文件」子文件夹，单页 PDF 直接放在类别文件夹里。
识别不出「电子发票」字样的 PDF 视为非电子发票文件，不复制，只在日志里列出。
"""

import os
import shutil
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

import pdfplumber
import tkinter as tk

# 提取函数失败时会逐个弹窗，批量扫描会被卡住；先保存 GUI 自己要用的 showwarning，再屏蔽
_gui_warn = messagebox.showwarning
messagebox.showwarning = lambda *args, **kwargs: None

from normal_invoice_recognition import extract_invoice_normal
from regular_invoice_recognition import extract_invoice_regular
from train_invoice_recognition import extract_invoice_train

CATEGORIES = ["火车票", "增值税发票", "普通发票", "其他发票"]
MULTI_PAGE_FOLDER = "多页文件"
DEFAULT_OUT = Path(__file__).resolve().parent / "temp" / "发票分类"


def is_electronic_invoice(page):
    """是否电子发票，判定直接复用 normal_invoice_recognition 里的「电子发票」字样匹配。"""
    return bool(extract_invoice_normal(page).get("is_invoice"))


def classify(page):
    """单页 PDF 归到四类之一；票面不是电子发票则返回 None（不归档）。

    判定顺序：先火车票，再普票/专票，剩下的靠「电子发票」字样兜底进"其他发票"。
    """
    if extract_invoice_train(page).get("is_train"):
        return "火车票"

    invoice_type = extract_invoice_regular(page).get("type")
    if invoice_type == "regular_invoice":
        return "普通发票"
    if invoice_type == "vat_special_invoice":
        return "增值税发票"
    return "其他发票" if is_electronic_invoice(page) else None


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
        self.update_idletasks()

    def run(self):
        folder = self.folder_var.get().strip().strip('"')
        search_root = Path(folder).resolve() if folder else None
        if search_root is None or not search_root.is_dir():
            _gui_warn("提示", "请先选择存在的文件夹")
            return

        self.run_btn.configure(state="disabled", text="分类中…")
        self.write(f"开始扫描：{search_root}")
        try:
            counts, multi_page, non_invoices, unreadable = collect(search_root, DEFAULT_OUT, self.write)
        except Exception as exc:
            self.write(f"扫描中断：{exc}")
            counts = {}
        finally:
            self.run_btn.configure(state="normal", text="开始分类")

        if counts:
            self.write(f"\n已归档 {sum(counts.values())} 个发票（其中多页 {multi_page} 个）到 {DEFAULT_OUT}")
            for name in CATEGORIES:
                self.write(f"  {name}: {counts[name]}")
            self.write(f"  非发票（未复制）: {len(non_invoices)}")
            self.write(f"  无法解析（未复制）: {len(unreadable)}")

    def open_out(self):
        DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
        os.startfile(DEFAULT_OUT)


if __name__ == "__main__":
    App().mainloop()
