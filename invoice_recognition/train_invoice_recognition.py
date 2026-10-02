import pdfplumber
import re
import unicodedata

from tkinter import messagebox


def extract_invoice_train(page):
    """
    从单页PDF中提取火车票关键字段。
    输入：pdfplumber.Page对象
    输出：dict， 即fields这一变量
    键值对如下: fields["is_train"]     是否火车票(通过查找电子客票号来判断是火车票还是普通发票)
              对于发票应该先判断这一键值对来判断是否是火车票，非火车票有且仅有一个键值对
              fields["E-ticket_number"]  电子客票号25位
              fields["invoice_no"]   发票号码20位    / fields["invoice_date"] 行程日期
              fields["invoice_time"] 发车时间       / fields["total_amount"] 价税合计金额
              fields["id"]           脱敏身份证号    / fields["person"]       乘车人姓名
              fields["site"] = [parts1[0], parts2[0]]   [0]起点站 [1]终点站
    """
    fields = {}

    # 提取页面全部文本，这个方法会把文字按阅读顺序拼接
    text = page.extract_text() or ""
    # 归一化：把PDF抽出的部首兼容字(如⼦ U+2FBA)还原成正常汉字(子 U+5B50)
    text = unicodedata.normalize("NFKC", text)
    # 把换行符保留下来，后面做逐行匹配会用到
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # 1. 判断是否是火车票，通过查找"电子客票号"来判断，非火车票直接退出
    is_train = False
    for ln in lines:
        m = re.search(r"\s*电\s*子\s*客\s*票\s*号\s*[:：]?\s*(\d{25})", ln)
        if m:
            is_train = True
            break

    fields["is_train"] = is_train
    if not is_train:
        return fields
    fields["E-ticket_number"] = re.sub(r"\s", "", m.group(1))

    # 2. 发票号码：通常以"发票号码"或"No."开头，后面跟一串数字
    invoice_no = None
    for ln in lines:
        #*匹配前面的子表达式0次或多次，？配位前面的子表达式0次或1次
        m = re.search(r"(?:发票号码|No\.?)\s*[:：]?\s*(\d{8,20})", ln)
        if m:
            invoice_no = re.sub(r"\s", "", m.group(1))
            break
    fields["invoice_no"] = invoice_no


    # 3. 行程日期：通常的格式是2024年05月18日
    date_str = None
    time_str = None
    for ln in lines:
        m = re.search(r"((\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*(\d{1,2}\s*[:：]?\s*\d{1,2}))", ln)
        if m:
            date_str = m.group(2)
            time_str = m.group(3)
            break
    if date_str is None or time_str is None:
        messagebox.showwarning("警告", "日期时间提取失败")
        return fields
    fields["invoice_date"] = re.sub(r"\s", "", date_str)
    fields["invoice_time"] = re.sub(r"\s", "", time_str)

    # 4. 价税合计金额：匹配"小写"那一行，通常格式为 ¥12345.67
    amount = None
    for ln in lines:
        m = re.search(r"([¥￥$]\s*[0-9]+\.*\d{0,2})", ln)
        if m:
            amount = re.sub(r"\s", "", m.group(1))  # 去除货币符号和数字之间的所有空白
            break
    fields["total_amount"] = amount

    # 5. 人员信息 4304261980****4379 xxx
    person = None
    id_number = None
    for ln in lines:
        m=re.search(r"((\d{10}\*\*\*\*\d{3}[0-9xX])\s*([^\n]*))",ln)
        if m:
            id_number=re.sub(r"\s", "", m.group(2))
            person=re.sub(r"\s", "", m.group(3))
            break
    fields["id"] = id_number
    fields["person"] = person

    # 6. 站点信息"xx"站->"xx"站
    box1 = (0,70,241,105)
    box2 = (329,69,page.width,105)
    station1 = page.crop(box1).extract_text()
    station2 = page.crop(box2).extract_text()
    station1 = unicodedata.normalize("NFKC", station1)
    station2 = unicodedata.normalize("NFKC", station2)
    if station1 is None or station2 is None:
        messagebox.showwarning("警告", "站点信息提取失败")
        return fields
    station1 = station1.strip()
    station2 = station2.strip()
    parts1 = station1.split("\n")
    parts2 = station2.split("\n")
    fields["site"] = [parts1[0], parts2[0]]


    return fields

if __name__ == "__main__":
    with pdfplumber.open("temp/火车票.pdf") as pdf:
        length=len(pdf.pages)
        if length==1:
            field=extract_invoice_train(pdf.pages[0])
            print(field)
        else:
            print("超过一页")
            messagebox.showwarning("警告", "PDF超过一页")