import pdfplumber
import re
import unicodedata

from tkinter import messagebox


def extract_invoice_train(page):
    """
    从单页PDF中提取火车票关键字段。
    输入：pdfplumber.Page对象
    输出：dict， 即fields这一变量
    键值对如下(按写入顺序编号):
        1.  fields["is_train"]        是否火车票(裁剪标题区域匹配"电子发票（铁路电子客票）"字样判断)
                                      非火车票直接返回，fields中仅含此一个键值对
        2.  fields["e_ticket_number"] 电子客票号25位
        3.  fields["state"]           客票状态(退票/补票/始发改签等，可能为None)
        4.  fields["invoice_no"]      发票号码
        5.  fields["invoice_date"]    行程日期
        6.  fields["invoice_time"]    发车时间
        7.  fields["total_amount"]    价税合计金额
        8.  fields["id"]              脱敏身份证号
        9.  fields["person"]          乘车人姓名
        10. fields["site"] = [起点站, 终点站]
    """
    fields = {}

    # 提取页面全部文本，这个方法会把文字按阅读顺序拼接
    text = page.extract_text() or ""
    # 归一化：把PDF抽出的部首兼容字(如⼦ U+2FBA)还原成正常汉字(子 U+5B50)
    text = unicodedata.normalize("NFKC", text)
    # 把换行符保留下来，后面做逐行匹配会用到
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    text_flow = page.extract_text(use_text_flow=True) or ""
    text_flow = unicodedata.normalize("NFKC", text_flow)
    text_flow_lines = [ln.strip() for ln in text_flow.splitlines() if ln.strip()]


    # 1. is_train判断是否是火车票，通过查找特定区域的"电子发票（铁路电子客票）"来判断，非火车票直接退出
    is_train = False
    box = (89, 8, (418 + page.width) / 2, 38)
    title_lines = page.crop(box).extract_text(use_text_flow=True) or ""
    title_lines = unicodedata.normalize("NFKC", title_lines)
    title_lines = [ln.strip() for ln in title_lines.splitlines() if ln.strip()]

    for ln in title_lines:
        m = re.search(r"\s*电\s*子\s*发\s*票\s*[（(]铁\s*路\s*电\s*子\s*客\s*票\s*[)）]", ln)
        if m:
            is_train = True
            break

    fields["is_train"] = is_train
    if not is_train:
        return fields

    # 2. 电子客票号e_ticket_number,应该为25位,
    #    记录state退票、补票、始发改签等状态
    e_ticket_number = None
    state = None
    for ln in text_flow_lines:
        m = re.search(r"\s*电\s*子\s*客\s*票\s*号\s*[:：]?\s*([^\n]{25})\s*([^\n]*)", ln)
        if m:
            e_ticket_number = re.sub(r"\s", "", m.group(1))
            if m.group(2):
                state = re.sub(r"\s", "", m.group(2))
            break

    fields["e_ticket_number"] = e_ticket_number
    fields["state"] = state

    # 3. invoice_no发票号码：通常以"发票号码"或"No."开头，后面跟一串数字
    invoice_no = None
    for ln in lines:
        #*匹配前面的子表达式0次或多次，？配位前面的子表达式0次或1次
        m = re.search(r"(?:发票号码|No\.?)\s*[:：]?\s*(\d{8,20})", ln)
        if m:
            invoice_no = re.sub(r"\s", "", m.group(1))
            break
    fields["invoice_no"] = invoice_no


    # 4. date_str、time_str行程日期：通常的格式是2024年05月18日
    date_str = None
    time_str = None
    for ln in lines:
        m = re.search(r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*(\d{1,2}\s*[:：]?\s*\d{1,2})", ln)
        if m:
            date_str = m.group(1)
            time_str = m.group(2)
            break
    if date_str is None or time_str is None:
        messagebox.showwarning("警告", "日期时间提取失败")
        return fields
    fields["invoice_date"] = re.sub(r"\s", "", date_str)
    fields["invoice_time"] = re.sub(r"\s", "", time_str)

    # 5. amount价税合计金额：匹配"小写"那一行，通常格式为 ¥12345.67
    amount = None
    for ln in lines:
        m = re.search(r"[¥￥$]\s*[0-9]+\.*\d{0,2}", ln)
        if m:
            amount = re.sub(r"\s", "", m.group(0))  # 去除货币符号和数字之间的所有空白
            break
    fields["total_amount"] = amount

    # 6. person、id_number人员信息 4304261980****4379 王伟
    person = None
    id_number = None
    for ln in lines:
        m=re.search(r"(\d{10}\*\*\*\*\d{3}[0-9xX])\s*([^\n]*)",ln)
        if m:
            id_number=re.sub(r"\s", "", m.group(1))
            person=re.sub(r"\s", "", m.group(2))
            break
    fields["id"] = id_number
    fields["person"] = person

    # 7. station1、station2站点信息"xx"站->"xx"站
    box1 = (0,70,241,105)
    box2 = (329,69,page.width,105)
    station1 = page.crop(box1).extract_text() or ""
    station2 = page.crop(box2).extract_text() or ""
    station1 = unicodedata.normalize("NFKC", station1)
    station2 = unicodedata.normalize("NFKC", station2)

    if station1 is None or station2 is None:
        messagebox.showwarning("警告", "站点信息提取失败")
        return fields
    station1 = station1.strip().split("\n")[0]
    station2 = station2.strip().split("\n")[0]

    fields["site"] = [station1, station2]

    return fields

if __name__ == "__main__":
    with pdfplumber.open("temp/典型火车票/26329199021000300418.pdf") as pdf:
        length=len(pdf.pages)
        if length==1:
            field=extract_invoice_train(pdf.pages[0])
            print(field)
        else:
            print("超过一页")
            messagebox.showwarning("警告", "PDF超过一页")