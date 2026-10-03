import pdfplumber
import re
import unicodedata

from tkinter import messagebox

def extract_invoice_VAT(page):
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

    # text_flow = page.extract_text(use_text_flow=True) or ""
    # text_flow = unicodedata.normalize("NFKC", text_flow)
    # text_flow_lines = [ln.strip() for ln in text_flow.splitlines() if ln.strip()]


    # 1. is_vat判断是否是增值税专用发票，通过查找特定区域的"电子发票（增值税专用发票）"来判断，非增值税直接退出
    is_vat=False
    box = (121,14,432,52)
    title_lines = page.crop(box).extract_text(use_text_flow=True) or ""
    title_lines = unicodedata.normalize("NFKC", title_lines)
    title_lines = [ln.strip() for ln in title_lines.splitlines() if ln.strip()]

    for ln in title_lines:
        m = re.search(r"\s*电\s*子\s*发\s*票\s*[(（]增\s*值\s*税\s*专\s*用\s*发\s*票\s*[）)]", ln)
        if m:
            is_vat = True
            break

    fields["is_vat"] = is_vat
    if not is_vat:
        return fields

    # 2.


    return fields


if __name__ == "__main__":
    with pdfplumber.open("temp/增值税1.pdf") as pdf:
        length=len(pdf.pages)
        field = extract_invoice_VAT(pdf.pages[0])
        print(field)
        if length==1:
            field=extract_invoice_VAT(pdf.pages[0])
            print(field)
        else:
            print("超过一页")
            messagebox.showwarning("警告", "PDF超过一页")