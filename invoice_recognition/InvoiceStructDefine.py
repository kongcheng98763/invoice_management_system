import re
from tkinter import messagebox

import unicodedata
from pdfplumber.table import Table

class InvoiceProjectItem:
    def __init__(self,name:str,amount:float,
                 tax_rate:float,tax_amount:float,
                 model:str| None = None,unit:str| None = None,
                 quantity:float| None = None,price:float| None = None):
        self.item = {
            "name": name,               #str
            "model": model,             #str    | None
            "unit": unit,               #str    | None
            "quantity": quantity,       #float  | None
            "price": price,             #float  | None
            "amount": amount,           #float
            "tax_rate": tax_rate,       #float
            "tax_amount": tax_amount,   #float
        }

class InvoiceProject:
    def __init__(self):
        # 实例变量，每个对象各自一份
        self.array=[]               #存储每一行的数据,array[0]表示第1行的相关数据
    
    def add(self,item:InvoiceProjectItem):
        self.array.append(item)

    def output(self):
        return tuple(self.array)

    # 报错展示,后续会使用winui3作为展示界面
    @staticmethod
    def error(message:str):
        messagebox.showwarning("警告",message)

    #获取明细表里的数据,输入:最大表
    def get_detail(self, table:Table|None,):
        if table is None:
            self.error("明细表为空,请检查发票是否包含明细表")
            return

        count=0
        area_table_detail=None
        for cell in table.rows[1].cells:
            if cell is None:  # 忽略合并单元格占位
                continue
            area_table_detail = cell
            count += 1
        if count != 1 or area_table_detail is None:
            self.error("最大表第二行有多个单元格,明细表检测失败")
            return

        # 增值税发票与普通发票明细表的上边框+4+9后,可以获取"项目名称,规格型号"等数据,
        # crop保留与框相交（部分落入）的文字，但文字的坐标框被截断到边界处，本次提取只需要提取x坐标即可，无需关注y坐标
        # -0.5为余量控制，项目部分不计入title中
        area_title = (area_table_detail[0], area_table_detail[1],
                      area_table_detail[2], area_table_detail[1] + 4 + 9-0.2)
        crop_title = table.page.crop(area_title)
        title = crop_title.extract_words()
        # NFKC 归一化 + 去空白：全角字符转半角（如"税率／征收率"的全角斜杠），
        # 并去掉词内残留空白，使后续与列名比较不受全角/半角、空白差异影响；
        # 本步在 extract_words 之后执行，不改变其分词结果
        for t in title:
            t["text"] = unicodedata.normalize("NFKC", t["text"])
            t["text"] = re.sub(r"\s", "", t["text"])


        # 列名 -> 标题词下标:extract_words 按空格分词,带空格的列名会被拆成多个词,
        # 下标个数 = 该列实际拆出的词数(1个/2个/3个...均可,新增列只需在表中加一行)
        title_columns = [
            ("项目名称", (0,)),
            ("规格型号", (1,)),
            ("单位", (2, 3)),
            ("数量", (4, 5)),
            ("单价", (6, 7)),
            ("金额", (8, 9)),
            ("税率/征收率", (10,)),
            ("税额", (11, 12)),
        ]

        # 下标越界守卫:所需单词数由表推导,以后改表不用改数字
        need = max(i for _, indices in title_columns for i in indices) + 1
        if len(title) < need:
            self.error(f"标题行仅提取到 {len(title)} 个单词,不足 {need} 个,明细表标题检测失败")
            return

        # 存储title中各个项目名称的边界
        boundary_title = {}
        for name, indices in title_columns:
            # text 已在上面统一去空白,这里直接拼接与列名核对,相对应才配对;
            # 坐标取第一个词的 x0 到最后一个词的 x1(单词列起止是同一下标)
            text = "".join(title[i]["text"] for i in indices)
            if text == name:
                boundary_title[name] = [title[indices[0]]["x0"], title[indices[-1]]["x1"]]
            else:
                self.error(f"标题'{name}'与提取内容不匹配,实际提取到:{text}")
                return

        # 调试用：格式化输出 boundary_title,核对每列的 x 坐标区间
        print(f"boundary_title 共 {len(boundary_title)} 列:")
        for name, (x0, x1) in boundary_title.items():
            print(f"  {name}: x0={x0:.2f} x1={x1:.2f} 宽={x1 - x0:.2f}")

        boundary_horizontal = [
        ["name", area_table_detail[0]                                          , boundary_title["规格型号"][0] - 1],  # 项目名称
        ["model", boundary_title["规格型号"][0]                                  , boundary_title["单位"][0] - 1],  # 规格型号
        ["unit", boundary_title["单位"][0]                                     , (boundary_title["单位"][1] + boundary_title["数量"][0]) / 2],  # 单位
        ["quantity", (boundary_title["单位"][1]+boundary_title["数量"][0])/2        , boundary_title["数量"][1]],  # 数量
        ["price", boundary_title["数量"][1] + 1                                 , boundary_title["单价"][1]],  # 单价
        ["amount", boundary_title["单价"][1] + 1                                 , boundary_title["金额"][0] - 1],  # 金额
        ["tax_rate", boundary_title["金额"][1] + 1                                 , (boundary_title["税率/征收率"][0] + boundary_title["税额"][0]) / 2],  # 税率/征收率
        ["tax_amount", (boundary_title["税率/征收率"][0]+boundary_title["税额"][0])/2,  area_table_detail[2]],  # 税额
        ]

        # 调试用：输出横向边界的数据结构、内容及实际变量类型
        print(
            f"boundary_horizontal: 容器类型={type(boundary_horizontal).__name__}, "
            f"共 {len(boundary_horizontal)} 列, 数据结构=list[list]"
        )
        for i, item in enumerate(boundary_horizontal):
            print(f"  [{i}] 元素类型={type(item).__name__}, 内容={item}")
            print(
                f"      字段类型: name={type(item[0]).__name__}, "
                f"left={type(item[1]).__name__}, "
                f"right={type(item[2]).__name__}"
            )

        #项目区域:标题行下沿(上边框+4+9)到最大表第二行单元格下边框
        area_project = (area_table_detail[0], area_table_detail[1] + 4 + 9,
                        area_table_detail[2], area_table_detail[3] - 10)

        #金额区域
        area_amount = (area_table_detail[0], area_table_detail[3] - 10 + 0.2,
                      area_table_detail[2], area_table_detail[3])




        #项目名称列的整列，含"项目名称","小计"(不一定存在),"合计"
        area_detail_project = (area_table_detail[0], area_table_detail[1],
                               boundary_title["规格型号"][0]-0.5, area_table_detail[3])#项目名称的区域
        crop_project_column = table.page.crop(area_detail_project)
        #project_column提取的是项目名称整列的文字
        project_column = crop_project_column.extract_words()
        if not project_column:
            self.error("项目名称列提取失败")
            return
        for p in project_column:
            p["text"] = unicodedata.normalize("NFKC", p["text"])
            p["text"] = re.sub(r"\s", "", p["text"])

        # 调试用：格式化输出项目名称整列各单词，便于核对列内文本与坐标
        print(f"project_column 共 {len(project_column)} 个单词:")
        for i, w in enumerate(project_column):
            print(f"  [{i}] text={w['text']} x0={w['x0']:.2f} x1={w['x1']:.2f} "
                  f"top={w['top']:.2f} bottom={w['bottom']:.2f} 宽={w['x1'] - w['x0']:.2f}")

        # project_boundary中对应的内容为：项目名称中各个项目的上下边界
        # project_boundary[0]和[-1] 应该为表格的上下边框线
        # project_boundary[1] 开始为项目的文字边界，若之后需要提取[2]中的文字，则需要[1]的下边界和[3]的上边界
        # project_boundary=[{"name":string, "top":float, "bottom":float}
        #                  {"name":string, "top":float, "bottom":float}, ... ]
        project_boundary=list()
        project_boundary_temp=list()#暂存最后两行[{合计...}{小计...}]
        project_boundary.append({"name":"上边界", "top":area_table_detail[1], "bottom":area_table_detail[1]})
        project_column_part = project_column.copy()
        if project_column[0]["text"]=="项目名称":
            project_boundary.append({"name":"项目名称",
                                     "top":project_column[0]["top"],
                                     "bottom":project_column[0]["bottom"]})
            project_column_part = project_column_part[1:]
        else:
            self.error("项目名称列第一行不是'项目名称'")
            return

        if project_column[-2]["text"]=="合" and project_column[-1]["text"]=="计":
            project_boundary_temp.append({"name":"合计",
                                     "top":min(project_column[-2]["top"], project_column[-1]["top"]),
                                     "bottom":max(project_column[-2]["bottom"], project_column[-1]["bottom"])})
            project_column_part = project_column_part[:-2]
        else:
            self.error("项目名称列最后两行不是'合计'")
            return

        if len(project_column) > 4 and project_column[-4]["text"]=="小" and project_column[-3]["text"]=="计":
            project_boundary_temp.append({"name":"小计",
                                     "top":min(project_column[-4]["top"], project_column[-3]["top"]),
                                     "bottom":max(project_column[-4]["bottom"], project_column[-3]["bottom"])})
            project_column_part = project_column_part[:-2]
        #现在project_column_part中只有 项目的的具体信息
        print(project_column_part)

        status = 0  # 0(未找到*)->1(找到1个*)->2(找到2个*)->0(完成一次项目的内容读取)
        project_item = ""
        size = list()
        # size=[ {"top":float, "bottom":float}
        #      {"top":float, "bottom":float}
        #      {"top":float, "bottom":float} ]

        for item in project_column_part:  # item是字典，多个字典item组成的列表为project
            size.append({"top":item["top"], "bottom":item["bottom"]})

            for ch in item["text"]:

                if ch != "*":
                    project_item += ch
                else:
                    if project_item != "" and project_item[-1] == "*":
                        project_item += ch
                        continue
                    if status < 2:
                        project_item += ch
                        status += 1
                    else:
                        project_boundary.append({"name":project_item, "top":size[0]["top"], "bottom":size[-2]["bottom"]})
                        size = [size[-1]]
                        project_item = "" + ch
                        status = 1
        project_boundary.append({"name":project_item, "top":size[0]["top"], "bottom":size[-1]["bottom"]})
        project_number= len(project_boundary)

        # 末尾补回页脚行边界:temp 按识别顺序压入(合计在前、小计在后),倒序追加才能还原文档中的上下顺序
        for boundary in reversed(project_boundary_temp):
            project_boundary.append(boundary)

        project_boundary.append({"name":"下边界", "top":area_table_detail[3], "bottom":area_table_detail[3]})

        # 调试用：格式化输出各项目文本及 top、bottom，便于核对项目切分结果
        # project_boundary 的元素是 dict（含 name/top/bottom），不能按下标解包
        print(f"project_boundary 共 {len(project_boundary)} 个项目:")
        for i, boundary in enumerate(project_boundary):
            print(f"  [{i}] 元素类型={type(boundary).__name__}, "
                  f"项目名称:{boundary['name']} "
                  f"top={boundary['top']:.2f} bottom={boundary['bottom']:.2f}, "
                  f"字段类型: name={type(boundary['name']).__name__}, "
                  f"top={type(boundary['top']).__name__}, "
                  f"bottom={type(boundary['bottom']).__name__}")

        # area_detail=[[InvoiceProjectItem]...]
        # InvoiceProjectItem=[{项目名称:string, 项目内容:string, 项目金额:float}, ...]
        # boundary_horizontal描述左右边界, project_boundary描述上下边界
        area_detail=list()
        for i in range(len(project_boundary)-1)[2 : project_number+1]:
            row=dict()
            row["name"]=project_boundary[i]["name"]
            for item in boundary_horizontal[1:]:
                area = (item[1], project_boundary[i]["top"], item[2], project_boundary[i+1]["top"]+2)
                crop = table.page.crop(area)

            crop_title = table.page.crop(area_title)
            title = crop_title.extract_words()
            # NFKC 归一化 + 去空白：全角字符转半角（如"税率／征收率"的全角斜杠），
            # 并去掉词内残留空白，使后续与列名比较不受全角/半角、空白差异影响；
            # 本步在 extract_words 之后执行，不改变其分词结果
            for t in title:
                t["text"] = unicodedata.normalize("NFKC", t["text"])
                t["text"] = re.sub(r"\s", "", t["text"])














        if len(table.rows) == 4:    # 正常情况，最大表有四行，但是也有可能备注跨页

            print()
            # crop_project = table.page.crop(area_detail_project)
            # project = crop_project.extract_words()
            # if not project:
            #     self.error("项目名称列提取失败")
            #     return
            #
            # for p in project:
            #     p["text"] = unicodedata.normalize("NFKC", p["text"])
            #     p["text"] = re.sub(r"\s", "", p["text"])
            #
            # print(project)
            # # 调试用：格式化输出项目名称列各单词，便于核对列内文本与坐标
            # print(f"project 共 {len(project)} 个单词:")
            # for i, w in enumerate(project):
            #     print(f"  [{i}] text={w['text']} x0={w['x0']:.2f} x1={w['x1']:.2f} "
            #           f"top={w['top']:.2f} bottom={w['bottom']:.2f} 宽={w['x1'] - w['x0']:.2f}")






        elif len(table.rows) == 2:  # 明细表跨页，最大表只有两行
            print()

        else:                       # 最大表行数异常
            self.error("最大表行数异常")
            return



