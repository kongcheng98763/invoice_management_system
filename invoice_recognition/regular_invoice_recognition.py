import pdfplumber
import re
import unicodedata

from tkinter import messagebox

def extract_invoice_regular(page):
    """
        从单页PDF中判断发票类型。
        输入：pdfplumber.Page对象
        输出：dict， 即fields这一变量
        键值对如下: fields["type"]  发票类型，取值有三种：
                  "regular_invoice"       电子发票(普通发票)
                  "vat_special_invoice"   电子发票(增值税专用发票)
                  None                    未匹配到已知发票类型
    """
    fields = {}

    # 提取页面全部文本，这个方法会把文字按阅读顺序拼接
    text = page.extract_text() or ""
    # 归一化：把PDF抽出的部首兼容字(如⼦ U+2FBA)还原成正常汉字(子 U+5B50)
    text = unicodedata.normalize("NFKC", text)
    # 把换行符保留下来，后面做逐行匹配会用到
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # 1. 判断是否是什么类型的发票,普通发票还是增值税专用发票
    is_regular = False
    box = (128,10,416,49)
    title_lines = page.crop(box).extract_text(use_text_flow=True) or ""
    title_lines = unicodedata.normalize("NFKC", title_lines)
    title_lines = [ln.strip() for ln in title_lines.splitlines() if ln.strip()]

    for ln in title_lines:
        m = re.search(r"\s*电\s*子\s*发\s*票[(（]\s*普\s*通\s*发\s*票\s*[）)]", ln)
        if m:
            is_regular = True
            break

    fields["is_regular"] = is_regular
    if not is_regular:
        return fields

    # 2. 开票日期和发票号码
    invoice_date = None
    invoice_number = None
    box1 = (432,17,page.width,69)
    info = page.crop(box1).extract_text() or ""
    info = unicodedata.normalize("NFKC", info)
    m1 = re.search(r"\s*开\s*票\s*日\s*期\s*[:：]?\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", info)
    m2 = re.search(r"\s*发\s*票\s*号\s*码\s*[:：]?\s*(\d{20})", info)
    if m1:
        invoice_date=re.sub(r"\s","",m1.group(1))
    else:
        messagebox.showwarning("警告", "未匹配到开票日期")
    if m2:
        invoice_number=re.sub(r"\s","",m2.group(1))
    else:
        messagebox.showwarning("警告", "未匹配到发票号码")

    fields["invoice_date"] = invoice_date
    fields["invoice_number"] = invoice_number

    return fields


if __name__ == "__main__":
    with pdfplumber.open("temp/普通发票1.pdf") as pdf:
        length=len(pdf.pages)
        field = extract_invoice_regular(pdf.pages[0])
        print(field)
        if length==1:
            field=extract_invoice_regular(pdf.pages[0])
            print(field)
        else:
            print("超过一页")
            messagebox.showwarning("警告", "PDF超过一页")