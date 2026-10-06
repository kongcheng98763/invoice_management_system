import pdfplumber
import re
import sys
import unicodedata

# 电子发票（普通发票）与电子发票（增值税专用发票）的项目明细表版式相同，这一模块被
# regular_invoice_recognition 和 VAT_invoice_recognition 共用。
#
# 标准票面八个栏目按 x 从左到右排：
#   项目名称 规格型号 单 位 数 量 单 价 金 额 税率/征收率 税 额
# 但实测票样里栏目会被拆成「项 目 名称」「单 位」这样带空格的词，有的票压根没有规格型号
# 和单位两栏（机场交通票），甚至单价排在数量前面，所以栏位和栏序一律从表头实测。

# (栏目名, 栏名)，按标准票面从左到右写；实际取用时只认票面出现的那几栏
HEADER_SLOTS = [
    ("项目名称", "item_name"),
    ("规格型号", "spec"),
    ("单位", "unit"),
    ("数量", "quantity"),
    ("单价", "unit_price"),
    ("金额", "amount"),
    ("税率/征收率", "tax_rate"),
    ("税额", "tax_amount"),
]
COLUMNS = [column for _, column in HEADER_SLOTS]
TEXT_COLUMNS = ("item_name", "spec")
# 金额、税率两栏齐全才算一条完整项目行：税额可以为空（不征税、免税行票面不填税额），
# 而换行续排的那半行既没有金额也没有税率，据此把续行和整行分开
ROW_MARKERS = ("amount", "tax_rate")
# 数字栏：票面的货币符、千分位逗号和词间空格都不进结果，只留数值本身
NUMERIC_COLUMNS = ("quantity", "unit_price", "amount", "tax_amount")
# 项目明细区到此结束的收尾行：小计、合计
END_RE = re.compile(r"^\s*(小\s*计|合\s*计)")
TOTAL_LINE_RE = re.compile(r"^\s*合\s*计")
HEADER_TEXT = "项目名称"
# 表头错位时并进税率栏的那种值：金额（两位小数）紧跟着税率，整串落在税率栏里
RATE_TAIL_RE = re.compile(r"^(?P<amount>\d+\.\d{2})(?P<rate>\d+(?:\.\d+)?%|不征税|免税|出口退税)$")
# 购销方信息、备注都在主表里，取到单元格文字后再从这里拆名称和税号。
# 票样里「名称：」和「统一社会信用代码/纳税人识别号：」的先后次序并不固定，
# 名称要吃到下一个栏目名为止，税号则直接认15~20位字母数字。
NAME_RE = re.compile(r"名\s*称\s*[:：]?\s*(.*?)(?=\s*(?:统一社会信用代码|纳税人识别号)|\s*[0-9A-Z]{15,20}|$)", re.S)
TAX_ID_RE = re.compile(r"(?:统一社会信用代码)?\s*/?\s*(?:纳税人识别号)?\s*[:：]?\s*([0-9A-Z]{15,20})")
# 大写金额写在「(大写)」这个标签之后，小写金额写在「(小写)」之后；小数点后只收两位，
# 否则去掉空白的整页文字里，金额会和后面备注里的编号连成一串
TOTAL_RE = re.compile(r"价税合计[（(]大写[)）](.*?)[（(]小写[)）][$¥￥]?(\d[\d,]*(?:\.\d{1,2})?)")
PAGES_RE = re.compile(r"共(\d+)页第(\d+)页")
# 开票人要按整行取：票面另有把同名再单独画一遍的签名行，去掉空白整页匹配会把两遍连成
# 「何京溶何京溶」，而 \S+ 更会一路吃到页尾（实测有样本取出 300 多个字）
DRAWER_RE = re.compile(r"^\s*开\s*票\s*人\s*[:：]?\s*(.+)$")
# 备注行按标签格认：去掉空白后「备注」出现在开头（真实票样里有合并格把上排的「姓」挤进来，
# 所以允许前面带两三个字），但项目名那种整串里才含「备」「注」的（备用件、注塑件）不算
REMARK_LABEL_RE = re.compile(r"^.{0,3}备注")


class InvoiceItem:
    """项目明细表里的一条开票项目，八个栏目一格一值，票面没有的栏为空串。

    换行续排（含跨页）的文字一律用 append_text 拼回所属栏目，且拼接时不加空格：
    *微电子组件*RS-485/RS-4 加上换行的 22芯片 才是完整的项目名称。
    """

    def __init__(self, **values):
        for column in COLUMNS:
            setattr(self, column, values.get(column, ""))

    def append_text(self, column, text):
        setattr(self, column, getattr(self, column) + text)

    def to_dict(self):
        return {column: getattr(self, column) for column in COLUMNS}


def normalize(s):
    # 归一化：把PDF抽出的部首兼容字(如⾦ U+2FAE)还原成正常汉字(金)，否则表头栏目名匹配不上
    return unicodedata.normalize("NFKC", s or "")


def squeeze(s):
    """去掉所有空白，用来认被拆开的表头行（「项 目 名称」和「项目名称」算同一行）"""
    return re.sub(r"\s", "", s)


def dedupe_page(page):
    """去掉同一位置被重画一遍的字。

    有些票用重影字做出假粗体，直接抽词会得到「*空空调调**加湿湿器器*」这种翻倍文字，
    数字也一样翻倍（9966..4466），分栏判定全废；普通票样没有重影，这步不改变字符。
    """
    return page.dedupe_chars()


def page_words(page):
    """整页的词，文字就地归一化；坐标保留 pdfplumber 的原值"""
    words = []
    for w in page.extract_words():
        w = dict(w)
        w["text"] = normalize(w["text"]).strip()
        if w["text"]:
            words.append(w)
    return words


def sorted_lines(page):
    """按 y 排好的文本行。

    extract_text_lines 返回的顺序并不严格按 y（竖排的「下载次数」「国 家 税 务 总 局」
    会插到表头行后面），不排的话这些文字会被当成项目行。
    """
    return sorted(page.extract_text_lines(), key=lambda ln: ln["top"])


class PageContext:
    """一页的公共中间结果：去重影后的页面、词、文本行、栏目、竖带和主表，都只算一遍。

    分栏、合计行、购销方、开票人都取自同一份词和行，各自 extract 一次会让一页被解析三四遍。
    """

    def __init__(self, page):
        self.page = dedupe_page(page)
        self.words = page_words(self.page)
        self.lines = sorted_lines(self.page)
        self.spans = column_spans(self.lines, self.words)
        self.ordered, self.bands = ([], {}) if self.spans is None else column_bands(self.spans)
        self.table = main_table(self.page)


_warnings = []


def warn(msg):
    """票面解析告警：只攒进清单，不弹窗。

    提取函数跑在后台线程或服务的工作线程里，Tk 不能碰、逐个对话框也会把扫描卡住；
    谁来展示这些告警由上层决定（tkinter 窗口收尾时统一列出来，服务把它们放进返回结果）。
    """
    _warnings.append(msg)


def take_warnings():
    """取走并清空累计的告警，供服务侧放进 report"""
    out = list(_warnings)
    del _warnings[:]
    return out


def header_band(lines, words):
    """认「项目名称 …」表头行，返回该行按 x 排好的词；票面没有这一行返回 None"""
    for line in lines:
        if squeeze(normalize(line["text"])).startswith(HEADER_TEXT):
            middle = (line["top"] + line["bottom"]) / 2
            band = [w for w in words if abs((w["top"] + w["bottom"]) / 2 - middle) < 4]
            return sorted(band, key=lambda w: w["x0"])
    return None


def column_spans(lines, words):
    """从表头实测每栏的横向范围 {栏名: (x0, x1)}，只配上票面里出现的那几栏，认不出返回 None。

    栏目名被拆成带空格的词（「单 位」「税率 /征收率」），所以要逐词累加去凑栏目名；
    「单」既可能开「单位」也可能开「单价」，凑不成当前栏就换下一栏重试。
    """
    tokens = header_band(lines, words)
    if tokens is None:
        return None

    spans = {}
    i = 0
    while i < len(tokens):
        matched = False
        for label, column in HEADER_SLOTS:
            if column in spans:
                continue
            buf, j = "", i
            while j < len(tokens):
                buf += tokens[j]["text"]
                if buf == label:
                    spans[column] = (tokens[i]["x0"], tokens[j]["x1"])
                    i, matched = j + 1, True
                    break
                if not label.startswith(buf):
                    break
                j += 1
            if matched:
                break
        if not matched:
            i += 1

    if "amount" not in spans:
        warn("未匹配到项目明细表头")
        return None
    return spans


def column_bands(spans):
    """实测到的栏按 x 排好，相邻栏取中点当竖带分界，返回 (栏顺序, {栏名: 竖带左边界})"""
    ordered = sorted(spans, key=lambda c: spans[c][0])
    bands = {}
    for i, column in enumerate(ordered):
        bands[column] = -float("inf") if i == 0 else (spans[ordered[i - 1]][1] + spans[column][0]) / 2
    return ordered, bands


def word_column(word, ordered, bands):
    """一个词落在哪一栏：按词的中心 x 找竖带。

    名称、规格型号靠左排、数字靠右排，取中心比取 x0 稳；
    换行续排的词（如「单口头+Type-C」的 x0 会比表头 x0 略小）也不至于错栏。
    """
    center = (word["x0"] + word["x1"]) / 2
    column = ordered[0]
    for candidate in ordered:
        if center >= bands[candidate]:
            column = candidate
    return column


def clean_value(column, text):
    """数字栏去掉货币符、千分位和空格；税率栏（13%、不征税）原样保留"""
    if column in NUMERIC_COLUMNS:
        return re.sub(r"[$¥￥,\s]", "", text)
    return text


def bucket_line(line, words, ordered, bands):
    """把一行的词按竖带分到各栏，栏内按 x 顺序连接，返回 {栏名: 文字}"""
    lo, hi = line["top"] - 1, line["bottom"] + 1
    picked = [w for w in words if lo <= (w["top"] + w["bottom"]) / 2 <= hi]
    buckets = {}
    for w in sorted(picked, key=lambda w: w["x0"]):
        buckets.setdefault(word_column(w, ordered, bands), []).append(w["text"])
    return {c: clean_value(c, "".join(v)) for c, v in buckets.items()}


def realign_amount(buckets):
    """把被挤进税率栏开头的金额退回金额栏。

    有的票表头整排比数据靠右（实测 109.pdf），按表头算出的竖带会把金额并进税率栏，
    得到「96.4613%」这种值；金额栏空着而税率栏尾部确实是税率时，前段数字就是金额。
    """
    rate = buckets.get("tax_rate") or ""
    if buckets.get("amount") or not rate:
        return buckets
    m = RATE_TAIL_RE.match(rate)
    if m:
        buckets["amount"] = m.group("amount")
        buckets["tax_rate"] = m.group("rate")
    return buckets


def parse_item_lines(ctx):
    """按票面顺序解析单页的项目明细区，返回 [(kind, payload), …]。

    kind 为 "row" 时 payload 是 InvoiceItem（金额、税率栏都有字），
    kind 为 "frag" 时 payload 是 {"item_name": …, "spec": …}，即上一行的换行续排；
    它可能接在本页最后一行之后，也可能接在上一页最后一行之后（跨页换行）。
    """
    if ctx.spans is None:
        return []
    required = [c for c in ROW_MARKERS if c in ctx.spans]

    lines = ctx.lines
    header_index = next((i for i, ln in enumerate(lines)
                         if squeeze(normalize(ln["text"])).startswith(HEADER_TEXT)), None)
    if header_index is None:
        return []

    records = []
    for line in lines[header_index + 1:]:
        text = squeeze(normalize(line["text"]))
        # 小计/合计是项目区的收尾行；接着的价税合计、出行人表都不算项目
        if END_RE.match(normalize(line["text"])) or text.startswith("价税合计"):
            break
        buckets = realign_amount(bucket_line(line, ctx.words, ctx.ordered, ctx.bands))
        if all(buckets.get(c) for c in required):
            records.append(("row", InvoiceItem(**{c: buckets.get(c, "") for c in COLUMNS})))
        else:
            records.append(("frag", {c: buckets.get(c, "") for c in TEXT_COLUMNS}))
    return records


def party_info(cell_text):
    """从购销方单元格里拆出名称和纳税人识别号"""
    name = NAME_RE.search(cell_text)
    tax_id = TAX_ID_RE.search(cell_text)
    return {"name": name.group(1).strip() if name else None,
            "tax_id": tax_id.group(1) if tax_id else None}


def main_table(page):
    """票面上格子最多的表格就是含购销方/项目/价税合计/备注的主表，印章框只有两格"""
    tables = page.find_tables()
    if not tables:
        return None
    return max(tables, key=lambda t: len(t.cells))


def row_texts(page, table, row_index):
    """读主表某一行的各单元格文字，合并单元格留下的 None 位置直接跳过"""
    texts = []
    for cell in table.rows[row_index].cells:
        if cell is None:
            continue
        # strict=False：表格线只要画出页面边界一丁点（实测火车票版式就有两格 y0 是 -0.002），
        # 默认的 strict=True 会直接抛 ValueError，整张票被判成「无法解析」；文字本来在页面里，夹回来照读
        texts.append(normalize(page.crop(cell, strict=False).extract_text() or "").replace("\n", " ").strip())
    return texts


def extract_invoice_item_fields(page):
    """
        解析单页电子发票的项目明细区、购销方、价税合计与备注。
        输入：pdfplumber.Page对象
        输出：dict， 即fields这一变量
        键值对如下(按写入顺序编号):
            1.  fields["records"]      本页项目明细区按票面顺序的行 [(kind, payload), …]，只交单行的结果，
                                       跨页换行的拼接由 extract_invoice_items 负责
            2.  fields["buyer"]        购买方 {"name","tax_id"}，主表第一行的第二格
            3.  fields["seller"]       销售方 {"name","tax_id"}，主表第一行的最后一格；
                                       普票主表第一行有合并格、专票没有，取最后一格两种票就统一了
            4.  fields["remark"]       备注格文字，本页没有备注为 None
            5.  fields["total_upper"]  价税合计大写，如 壹佰零玖圆整
            6.  fields["total_amount"] 价税合计小写金额，如 109.00
            7.  fields["amount_total"] 合计行的金额列，如 96.46
            8.  fields["tax_total"]    合计行的税额列，如 12.54
            9.  fields["page_count"] / fields["page_no"]  共X页 / 第Y页，票面没写则为 None
            10. fields["drawer"]       开票人
    """
    fields = {}
    ctx = PageContext(page)

    # 1. 项目明细区
    fields["records"] = parse_item_lines(ctx)

    text = squeeze(normalize(ctx.page.extract_text()))

    # 2/3/4. 购销方与备注都在主表里：第一行最后一格必是销售方，第二格是购买方；
    #        备注行的特征是首格竖排写成「备 注」两个字
    buyer = seller = remark = None
    if ctx.table is not None:
        for i in range(len(ctx.table.rows)):
            texts = row_texts(ctx.page, ctx.table, i)
            if not texts:
                continue
            if i == 0 and len(texts) >= 2:
                buyer, seller = party_info(texts[1]), party_info(texts[-1])
            if len(texts) >= 2 and REMARK_LABEL_RE.match(squeeze(texts[0])):
                remark = texts[1] or None
    fields["buyer"] = buyer
    fields["seller"] = seller
    fields["remark"] = remark

    # 5/6. 价税合计：大写接着（小写）¥xxx，同一行里两样都在
    m = TOTAL_RE.search(text)
    fields["total_upper"] = m.group(1) if m else None
    fields["total_amount"] = m.group(2) if m else None

    # 7/8. 合计行的金额、税额两栏，同样用表头算出的竖带来定位
    amount_total = tax_total = None
    if ctx.spans is not None:
        for line in ctx.lines:
            if not TOTAL_LINE_RE.match(normalize(line["text"])):
                continue
            buckets = bucket_line(line, ctx.words, ctx.ordered, ctx.bands)
            amount_total, tax_total = buckets.get("amount"), buckets.get("tax_amount")
            break
    fields["amount_total"] = amount_total
    fields["tax_total"] = tax_total

    # 9/10. 页码与开票人
    m = PAGES_RE.search(text)
    fields["page_count"] = int(m.group(1)) if m else None
    fields["page_no"] = int(m.group(2)) if m else None
    drawer = None
    for line in ctx.lines:
        m = DRAWER_RE.match(normalize(line["text"]).strip())
        if m:
            drawer = m.group(1).strip()
            break
    fields["drawer"] = drawer

    return fields


SIMPLE_KEYS = ["buyer", "seller", "total_upper", "total_amount", "amount_total", "tax_total", "page_count", "drawer"]
# 购销方、页码、开票人各页都一样，取第一个；合计类要以最后一页为准——
# 有的票每页都印一行小计/合计，取第一页会拿到只算到该页的局部值
FIRST_KEYS = ["buyer", "seller", "page_count", "drawer"]
LAST_KEYS = ["total_upper", "total_amount", "amount_total", "tax_total"]


def extract_invoice_items(pages):
    """
        逐页解析一份（可能多页的）电子发票，把跨页换行的项目名称/规格型号拼回上一条项目。
        第一页排满项目、第二页才出现价税合计的票，合计和备注都按页序收齐。
        输入：pdfplumber 的 pages 列表
        输出：dict， 即fields这一变量
        键值对如下:
            1.  fields["item_list"]    项目列表，每个元素是 InvoiceItem.to_dict()
            2.  fields["buyer"]        购买方 {"name","tax_id"}
            3.  fields["seller"]       销售方 {"name","tax_id"}
            4.  fields["remark"]       各页备注按页序拼接
            5.  fields["total_upper"] / fields["total_amount"]      价税合计大写/小写
            6.  fields["amount_total"] / fields["tax_total"]        合计行金额/税额
            7.  fields["page_count"] / fields["drawer"]             共X页 / 开票人
    """
    items = []
    last = None
    remarks = []
    fields = {key: None for key in SIMPLE_KEYS}
    # 票面印的「共X页第Y页」是连续的；合并票样时 Y 会回到 1，这是「一份 PDF 装了不止一张票」的唯一现场证据
    printed_page_no = None

    for page_no, page in enumerate(pages, 1):
        page_fields = extract_invoice_item_fields(page)
        printed = page_fields.get("page_no")
        if printed is not None:
            if printed_page_no is not None and printed <= printed_page_no:
                warn(f"第{page_no}页票面页码是「第{printed}页」，没有接上一页的「第{printed_page_no}页」："
                     f"这份 PDF 里像是装了不止一张票，解析仍按一张票拼接，票头和合计会分属不同票")
            printed_page_no = printed
        for kind, payload in page_fields["records"]:
            if kind == "row":
                items.append(payload)
                last = payload
                continue
            if last is None:
                warn(f"第{page_no}页的续排文字没有可归属的项目")
                continue
            # 续排文字：项目名称和规格型号谁有字就拼到谁身上
            for column, text in payload.items():
                if text:
                    last.append_text(column, text)
        if page_fields["remark"]:
            remarks.append(page_fields["remark"])
        for key in FIRST_KEYS:
            if fields[key] is None:
                fields[key] = page_fields[key]
        for key in LAST_KEYS:
            if page_fields[key] is not None:
                fields[key] = page_fields[key]

    fields["item_list"] = [item.to_dict() for item in items]
    fields["remark"] = "\n".join(remarks) if remarks else None
    return fields


if __name__ == "__main__":
    # Windows 控制台默认 GBK，票面里的 ¥ 和兼容字会打印报错，先把输出切成 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
    with pdfplumber.open("temp/普通发票_多页不连续.pdf") as pdf:
        result = extract_invoice_items(pdf.pages)
        print(f"项目 {len(result['item_list'])} 条，合计 金额={result['amount_total']} 税额={result['tax_total']}")
        for item in result["item_list"]:
            print(f"  {item['item_name']} | {item['spec']} | {item['unit']} {item['quantity']} "
                  f"{item['unit_price']} {item['amount']} {item['tax_rate']} {item['tax_amount']}")
        print(f"购买方={result['buyer']}")
        print(f"销售方={result['seller']}")
        print(f"价税合计={result['total_upper']} {result['total_amount']} 开票人={result['drawer']}")
        print(f"备注={result['remark']}")
