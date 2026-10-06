import pdfplumber
import re
import sys
import unicodedata

from invoice_item_recognition import extract_invoice_item_fields, extract_invoice_items, warn


def extract_invoice_regular_header(page):
    """
        只认票头：是否电子发票(普通发票)，是则再取开票日期和发票号码，不碰项目明细区。
        单页完整解析用 extract_invoice_regular，多页用 extract_invoice_regular_pages
        （它只要票头 + 跨页汇总，票头单独拆出来才不会把第一页解析两遍）。
        输入：pdfplumber.Page对象
        输出：dict， 即fields这一变量
        键值对如下:
            1.  fields["is_regular"]     是否普通发票，为 False 时只含这一个键
            2.  fields["invoice_date"]   开票日期，如 2026年06月08日
            3.  fields["invoice_number"] 发票号码，20位
    """
    fields = {}

    # 1. 判断是什么类型的发票：普通发票还是增值税专用发票
    is_regular = False
    box = (128,10,416,49)
    title_lines = page.crop(box, strict=False).extract_text(use_text_flow=True) or ""
    # 归一化：把PDF抽出的部首兼容字(如⼦ U+2FBA)还原成正常汉字(子 U+5B50)
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
    info = page.crop(box1, strict=False).extract_text() or ""
    info = unicodedata.normalize("NFKC", info)
    m1 = re.search(r"\s*开\s*票\s*日\s*期\s*[:：]?\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", info)
    m2 = re.search(r"\s*发\s*票\s*号\s*码\s*[:：]?\s*((\d\s*){20})", info)
    if m1:
        invoice_date = re.sub(r"\s", "", m1.group(1))
    else:
        warn("未匹配到开票日期")
    if m2:
        invoice_number = re.sub(r"\s", "", m2.group(1))
    else:
        warn("未匹配到发票号码")

    fields["invoice_date"] = invoice_date
    fields["invoice_number"] = invoice_number
    return fields


def extract_invoice_regular(page):
    """
        从单页PDF中判断发票类型，是普通发票再把项目明细等票面字段一并取出来。
        输入：pdfplumber.Page对象
        输出：dict， 即fields这一变量
        键值对如下:
            1.  票头三键 is_regular / invoice_date / invoice_number，见 extract_invoice_regular_header
            2.  其余键与 invoice_item_recognition.extract_invoice_item_fields 相同：
               records / buyer / seller / remark / total_upper / total_amount /
               amount_total / tax_total / page_count / page_no / drawer
        多页发票的逐页解析和跨页拼接用 extract_invoice_regular_pages。
    """
    fields = extract_invoice_regular_header(page)
    if not fields.get("is_regular"):
        return fields

    # 项目明细、购销方、价税合计、备注：普票和专票这几栏版式相同，解析都放在
    # invoice_item_recognition，销售方取主表第一行最后一个单元格
    fields.update(extract_invoice_item_fields(page))
    return fields


def extract_invoice_regular_pages(pages):
    """
        逐页解析一份（可能多页的）电子发票(普通发票)。
        输入：pdfplumber 的 pages 列表
        输出：dict， 即fields这一变量，键值同 invoice_item_recognition.extract_invoice_items
                  （item_list / buyer / seller / remark / total_upper / total_amount /
                   amount_total / tax_total / page_count / drawer），
                  另外带上第一页判出来的 is_regular / invoice_date / invoice_number
    """
    fields = extract_invoice_regular_header(pages[0])
    fields.update(extract_invoice_items(pages))
    return fields


if __name__ == "__main__":
    # Windows 控制台默认 GBK，票面里的 ¥ 会打印报错，先把输出切成 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
    with pdfplumber.open("temp/普通发票_多页不连续.pdf") as pdf:
        field = extract_invoice_regular_pages(pdf.pages)
        print({k: v for k, v in field.items() if k != "item_list"})
        print(f"项目 {len(field['item_list'])} 条，第一、最后一条：")
        for item in (field["item_list"][:1] + field["item_list"][-1:]):
            print("  ", item)
