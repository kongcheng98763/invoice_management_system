import os
import sys
import tkinter as tk
from tkinter import filedialog

import pdfplumber
from PIL import ImageFont

from text_debug import process_pdf as process_text_pdf

# 画中文需要支持 CJK 的字体，Windows 下用微软雅黑；拿不到则回退默认(只能显示数字)
try:
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 80)
    font_small = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 28)
except OSError:
    font = None
    font_small = None

# 不同单元格用不同的填充色（RGB，实际绘制时附加 alpha 做成半透明）
COLORS = [
    (255, 0, 0), (0, 0, 255), (0, 200, 0), (255, 165, 0), (128, 0, 128),
    (0, 200, 200), (255, 0, 255), (165, 42, 42), (0, 0, 128), (50, 205, 50),
]


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
    """对单个 PDF 指定页做表格单元格坐标调试渲染，并输出坐标文本。"""
    pdf_name = os.path.splitext(os.path.basename(path))[0]
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_index]
        im = page.to_image(resolution=800)

        # 查看 table 坐标：find_tables 返回 Table 对象，含整表 bbox 和每个单元格 cells
        tables = page.find_tables()
        out = []  # 收集坐标信息，最后写入 txt

        def log(s=""):
            print(s)
            out.append(s)

        log(f"页面尺寸: width={page.width}, height={page.height}")
        log("坐标含义: (x0, top, x1, bottom) — x0=左边界, top=上边界, x1=右边界, bottom=下边界")
        log("坐标系: 单位=pt(磅); 原点在页面左上角, x 向右增大、top/bottom 向下增大; 宽=x1-x0, 高=bottom-top")
        log(f"共识别到 {len(tables)} 个表格")

        for i, t in enumerate(tables):
            log(f"\n[表格 {i}] 单元格数={len(t.cells)}")

            # 每个单元格坐标（不同填充色 + 图上标出序号与完整坐标）
            for j, cell in enumerate(t.cells):
                if not cell:
                    continue
                rgb = COLORS[j % len(COLORS)]
                # fill 带 alpha(90) 为半透明，不遮住底层文字；stroke 用同色实边
                im.draw_rect(cell, fill=(*rgb, 90), stroke=rgb, stroke_width=2)
                cx0, ctop, cx1, cbottom = cell
                log(f"    cell[{j}]: x0={cx0:.1f}(左) top={ctop:.1f}(上) x1={cx1:.1f}(右) bottom={cbottom:.1f}(下)"
                    f"  宽={cx1 - cx0:.1f} 高={cbottom - ctop:.1f}")
                # 编号居中(大字号)，坐标以小字号紧跟在编号后面
                ccx, ccy = im._reproject(((cx0 + cx1) / 2, (ctop + cbottom) / 2))
                num = f"c{j}"
                im.draw.text((ccx, ccy), num, fill="black", font=font, anchor="mm")
                nw = im.draw.textbbox((0, 0), num, font=font)[2]  # 编号像素宽
                coord_str = f"({cx0:.0f},{ctop:.0f},{cx1:.0f},{cbottom:.0f})"
                im.draw.text((ccx + nw / 2 + 6, ccy), coord_str, fill="black", font=font, anchor="lm")

        im.save(f"temp/{pdf_name}table_debug.png")
        log(f"\n已保存调试图: temp/{pdf_name}table_debug.png (不同颜色=不同单元格坐标)")

        # 将坐标信息输出到 temp/<pdf名>table_cells.txt
        with open(f"temp/{pdf_name}table_cells.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        print(f"已保存坐标文本: temp/{pdf_name}table_cells.txt")


# 调试目标页码（从 0 开始）：text 与 table 都在这一页上识别
PAGE_INDEX = 0


def main():
    # Windows 控制台默认 GBK，提取的文字含 ¥ 等字符会打印报错，改 stdout 为 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")

    paths = select_files()
    if not paths:
        print("未选择任何文件，已退出。")
        return

    print(f"共选择 {len(paths)} 个文件：")
    for p in paths:
        print(f"\n===== 处理 {p} (第 {PAGE_INDEX + 1} 页) =====")
        process_text_pdf(p, PAGE_INDEX)  # 同一页：text_debug（单词框）
        process_pdf(p, PAGE_INDEX)  # 同一页：table_debug（表格单元格）


if __name__ == "__main__":
    main()
