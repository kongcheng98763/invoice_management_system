import pdfplumber
import re
import unicodedata

from InvoiceStructDefine import InvoiceProject
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
        m = re.search(r"\s*电\s*子\s*发\s*票\s*[(（]\s*普\s*通\s*发\s*票\s*[）)]", ln)
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
    m2 = re.search(r"\s*发\s*票\s*号\s*码\s*[:：]?\s*((\d\s*){20})", info)
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

    # 3. invoice_seller 销售方信息（用 rows/columns 取第一行最右cell）
    #    可能会出现单元格None的情况，由于发票的工整性，可以直接忽略列合并的情况，即忽略每一行的None单元格
    invoice_seller = None
    invoice_biggest_table = page.find_table()
    if invoice_biggest_table is None:
        messagebox.showwarning("警告", "未找到主表格")
        return fields

    # 只遍历第一行：直接取 rows[0]，不再用外层 for 循环
    count = 1
    for cell in invoice_biggest_table.rows[0].cells:
        if cell is None:  # 忽略合并单元格占位
            continue
        if count == 4:
            txt = page.crop(cell).extract_text() or ""
            txt = unicodedata.normalize("NFKC", txt)
            lns = [ln.strip() for ln in txt.splitlines() if ln.strip()]
            for ln in lns:
                m = re.search(r"名\s*称\s*[:：]\s*([^\n]*)", ln)
                if m:
                    invoice_seller = re.sub(r"\s", "", m.group(1))
                    break
                else:
                    messagebox.showwarning("警告", "未匹配到销售方信息")
                    return fields
            break
        count += 1
    fields["invoice_seller"] = invoice_seller

    # 4. invoice_project
    invoice_project = InvoiceProject()
    invoice_project.get_detail(invoice_biggest_table)


    return fields



if __name__ == "__main__":
    with pdfplumber.open("temp/增值税_多页不连续.pdf") as pdf:
        length=len(pdf.pages)
        invoice_project = InvoiceProject()
        invoice_project.get_detail(pdf.pages[0].find_table())
        # field = extract_invoice_regular(pdf.pages[0])
        # print(field)
        # if length==1:
        #     field=extract_invoice_regular(pdf.pages[0])
        #     print(field)
        # else:
        #     print("超过一页")
        #     messagebox.showwarning("警告", "PDF超过一页")