import pdfplumber
import re
import unicodedata

from tkinter import messagebox

def extract_invoice_normal(page):
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
    is_invoice = False
    for ln in lines:
        m = re.search(r"\s*电\s*子\s*发\s*票", ln)
        if m:
            is_invoice = True
            break

    fields["is_invoice"] = is_invoice

    return fields