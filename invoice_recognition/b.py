import pdfplumber
import sys
from PIL import ImageFont

# Windows 控制台默认 GBK，提取的文字含 ¥ 等字符会打印报错，改 stdout 为 UTF-8
sys.stdout.reconfigure(encoding="utf-8")

path = "temp/16、铁棒直径15,16mm×100mm.pdf"

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

with pdfplumber.open(path) as pdf:
    page = pdf.pages[0]
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

    im.save("temp/debug_table1.png")
    log(f"\n已保存调试图: temp/debug_table.png (不同颜色=不同单元格坐标)")

    # 将坐标信息输出到 temp/table_cells.txt
    with open("temp/table_cells.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("已保存坐标文本: temp/table_cells.txt")
