import pdfplumber
from PIL import ImageFont

with pdfplumber.open("temp/火车票.pdf") as pdf:
    page = pdf.pages[0]

    # 渲染页面为高分辨率图片，同时把单词边框和字符位置画上去
    im = page.to_image(resolution=300)
    im.draw_rects(page.rects, stroke="red", stroke_width=1)
    words = page.extract_words()
    im.draw_rects(words, stroke="blue", stroke_width=1)

    # 把 words 写入文本：每个单词一段，段内每个键值对占一行，并附加宽/高差值
    with open("temp/words.txt", "w", encoding="utf-8") as f:
        for i, w in enumerate(words):
            f.write(f"===== word {i} =====\n")
            for k, v in w.items():
                f.write(f"{k}: {v}\n")
            f.write(f"x1-x0: {w['x1'] - w['x0']:.2f}\n")        # 宽
          # f.write(f"y1-y0: {w['y1'] - w['y0']:.2f}\n")
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

    im.save("debug_page.png")


with pdfplumber.open("temp/火车票.pdf") as pdf:
    page=pdf.pages[0]
    text=page.extract_text()
    print(text)