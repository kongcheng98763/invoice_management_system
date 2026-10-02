import pdfplumber
import re
import unicodedata

from tkinter import messagebox

def extract_invoice_normal(page):
    """
        从单页PDF中提取火车票关键字段。
        输入：pdfplumber.Page对象
        输出：dict， 即fields这一变量
        键值对如下: fields["is_train"]     是否火车票(通过查找电子客票号来判断是火车票还是普通发票)
                  对于发票应该先判断这一键值对来判断是否是火车票，非火车票有且仅有一个键值对
                  fields["invoice_no"]   发票号码    / fields["invoice_date"] 行程日期
                  fields["invoice_time"] 发车时间    / fields["total_amount"] 价税合计金额
                  fields["id"]           脱敏身份证号 / fields["person"]       乘车人姓名
                  fields["site"]         站点
                  VAT special invoice   增值税专用发票
    """
    fields = {}

    # 提取页面全部文本，这个方法会把文字按阅读顺序拼接
    text = page.extract_text() or ""
    # 归一化：把PDF抽出的部首兼容字(如⼦ U+2FBA)还原成正常汉字(子 U+5B50)
    text = unicodedata.normalize("NFKC", text)
    # 把换行符保留下来，后面做逐行匹配会用到
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # 1. 判断是否是火车票，通过查找"电子客票号"来判断，非火车票直接退出
    type = None
    for ln in lines:
        m1 = re.search(r"\s*电\s*子\s*发\s*票\s*[（(]\s*普\s*通\s*发\s*票\s*[)）]", ln)
        m2 = re.search(r"\s*电\s*子\s*发\s*票\s*[（(]\s*增\s*值\s*税\s*专\s*用\s*发\s*票\s*[)）]", ln)
        if m1:
            type = "regular_invoice"
            break
        if m2:
            type = "vat_special_invoice"
            break
    fields["type"]=type


    return fields


if __name__ == "__main__":
    with pdfplumber.open("temp/dzfp_26332000007587400606_台州学院_20260902183915.pdf") as pdf:
        length=len(pdf.pages)
        field = extract_invoice_normal(pdf.pages[0])
        print(field)
        if length==1:
            field=extract_invoice_normal(pdf.pages[0])
            print(field)
        else:
            print("超过一页")
            messagebox.showwarning("警告", "PDF超过一页")