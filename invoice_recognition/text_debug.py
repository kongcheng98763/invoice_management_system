import os
import sys
import tkinter as tk
from tkinter import filedialog

import pdfplumber
from PIL import ImageFont


def select_files():
    """弹出简单 GUI 文件选择框，支持选择单个或多个 PDF 文件。"""
    root = tk.Tk()
    root.withdraw()  # 隐藏主窗口，只要文件选择对话框
    root.attributes("-topmost", True)  # 让对话框显示在最前面
    paths = filedialog.askopenfilenames(
        title="选择要调试的 PDF 文件（可多选）",
        initialdir="temp",
        filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
    )
    root.destroy()
    return list(paths)


def process_pdf(path, page_index=0):
    """对单个 PDF 指定页做单词框调试渲染，并打印提取文本。"""
    pdf_name = os.path.splitext(os.path.basename(path))[0]
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_index]

        # 渲染页面为高分辨率图片，同时把单词边框和字符位置画上去
        im = page.to_image(resolution=300)
        im.draw_rects(page.rects, stroke="red", stroke_width=1)
        words = page.extract_words()
        im.draw_rects(words, stroke="blue", stroke_width=1)

        # 把 words 写入文本：每个单词一段，段内每个键值对占一行，并附加宽/高差值
        with open(f"temp/{pdf_name}words.txt", "w", encoding="utf-8") as f:
            for i, w in enumerate(words):
                f.write(f"===== word {i} =====\n")
                for k, v in w.items():
                    f.write(f"{k}: {v}\n")
                f.write(f"x1-x0: {w['x1'] - w['x0']:.2f}\n")        # 宽
                #f.write(f"y1-y0: {w['y1'] - w['y0']:.2f}\n")
                f.write(f"bottom-top: {w['bottom'] - w['top']:.2f}\n")  # 高(纵向，pdfplumber无y0/y1)
                f.write("\n")
        # 在每个单词框左上角标出它的坐标 (x0, top)
        # 画中文需要支持 CJK 的字体，Windows 下用微软雅黑；拿不到字体则回退默认(只能显示数字)
        try:
            font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 12)
        except OSError:
            font = None
        for w in words:
            px, py = im._reproject((w["x0"], w["top"]))  # PDF坐标 -> 像素坐标
            width = w["x1"] - w["x0"]        # 宽
            height = w["bottom"] - w["top"]  # 高
            label = f"{w['text']} ({w['x0']:.0f},{w['top']:.0f}) {width:.0f}x{height:.0f}"
            im.draw.text((px, max(py - 13, 0)), label, fill="green", font=font)

        im.save(f"temp/{pdf_name}text_debug.png")

    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_index]
        text = page.extract_text(use_text_flow=False)
        print(text)


def main():
    # Windows 控制台默认 GBK，提取的文字含 ¥ 等字符会打印报错，改 stdout 为 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")

    paths = select_files()
    if not paths:
        print("未选择任何文件，已退出。")
        return

    print(f"共选择 {len(paths)} 个文件：")
    for p in paths:
        print(f"\n===== 处理 {p} =====")
        process_pdf(p)


if __name__ == "__main__":
    main()
