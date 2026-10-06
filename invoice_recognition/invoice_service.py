"""发票识别本地服务 —— need.md 3.1 方案 A：Python 侧常驻 FastAPI，WinUI3 用 REST + WebSocket 调它。

用法（在 invoice_recognition 目录下）：
    uv run uvicorn invoice_service:app --host 127.0.0.1 --port 8765

界面（winui\\Pages\\InvoicePage）POST /invoices/classify 触发扫描，扫描过程中的日志边跑边用
WebSocket 推回去；POST 的响应同时给出完整结果，这样 WebSocket 没连上时界面也能渲染。
扫描目标可以是整个文件夹（folder），也可以是界面拖进来的一批文件（files）——
后者是从微信聊天窗口直接拽 PDF 进来的场景，不必先把文件存到某个目录再选。
归档仍然只做复制、不动原件，目录规则和 classify_invoices.py 完全一致（复用它的 collect）。
"""
import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

import classify_invoices as ci
from invoice_item_recognition import take_warnings

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
# 默认和 tkinter 那个窗口用同一个归档目录；要试跑就指到别处，免得往正式归档里堆副本
OUT_DIR = Path(os.environ.get("INVOICE_OUT_DIR", ci.DEFAULT_OUT))
MAX_WARNINGS = 50
# 一次拖几万个文件没意义，先把上限钉住，免得请求把扫描线程占死
MAX_FILES = 2000

app = FastAPI(title="发票识别服务")

# 归档目录、告警清单和 WebSocket 广播都是共享的，一次只跑一个扫描
_scan_lock = asyncio.Lock()


class ScanRequest(BaseModel):
    """folder：要递归扫描的文件夹；files：界面拖进来的文件路径清单，给了 files 就以它为准"""

    folder: str = ""
    files: list[str] = []


class Broadcaster:
    """已连接的 WebSocket 集合。

    扫描跑在工作线程里（asyncio.to_thread），推消息要回投到事件循环才能安全地发给界面。
    """

    def __init__(self):
        self.clients = set()
        self.loop = None

    async def send(self, payload):
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                self.clients.discard(ws)

    def push(self, payload):
        if self.loop and self.clients:
            asyncio.run_coroutine_threadsafe(self.send(payload), self.loop)

    def log(self, line):
        self.push({"kind": "log", "line": line})


broadcaster = Broadcaster()


def scan(root, lines, files):
    """跑一遍分类归档，返回界面要的 ClassifyReport；日志同时逐行推给 WebSocket"""
    counts, multi_page, non_invoices, unreadable, entries = ci.collect(
        root, OUT_DIR, lambda line: (lines.append(line), broadcaster.log(line)),
        with_fields=True, files=files)
    # 归档目录里已有的副本不是「非发票」，把它们单列出来，否则界面汇总会说成没识别出来
    already = [e for e in entries if (e.get("note") or "").startswith("副本已在归档目录")]
    return {
        "out_dir": str(OUT_DIR),
        "multi_page": multi_page,
        "counts": counts,
        # 界面上这两类只显示数量，明细在 files 里（category 为空、原因写在 note）
        "non_invoices": max(len(non_invoices) - len(already), 0),
        "already_archived": len(already),
        "unreadable": len(unreadable),
        "files": entries,
        "log": [],
    }


@app.get("/health")
def health():
    return {"status": "ok", "out_dir": str(OUT_DIR), "categories": ci.CATEGORIES}


@app.post("/invoices/classify")
async def classify(request: ScanRequest):
    files = [f for f in request.files if f.strip()]
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"一次最多处理 {MAX_FILES} 个文件，收到 {len(files)} 个")
    # 相对路径会按服务进程的工作目录去解析，静默读到另一个无关文件；界面拖来的必然是绝对路径
    relative = [f for f in files if not os.path.isabs(f)]
    if relative:
        raise HTTPException(status_code=400, detail=f"文件路径必须是绝对路径，如 {relative[0]!r} 等 {len(relative)} 个")
    root = Path(request.folder) if request.folder.strip() else None
    if not files and (root is None or not root.is_dir()):
        raise HTTPException(status_code=400, detail=f"文件夹不存在且没有可用文件：{request.folder}")

    async with _scan_lock:
        broadcaster.loop = asyncio.get_running_loop()
        take_warnings()          # 丢掉上一轮残留的告警
        lines = []
        # 逐个 PDF 解析要几秒到几分钟，放到工作线程里跑，事件循环才能同时把日志推给界面
        report = await asyncio.to_thread(scan, root, lines, files)
        # 提取函数里的告警在批量扫描时被换成了空操作，不在这里收进 report 就彻底没人知道
        warnings = take_warnings()
        report["warnings"] = warnings[:MAX_WARNINGS]
        report["warning_count"] = len(warnings)
        # 有 WebSocket 在线时日志走推送（边跑边出），响应里再带一份会把同一行写两遍；
        # 界面没连上推送就把日志随响应带回，免得什么都不显示
        if not broadcaster.clients:
            report["log"] = lines
        broadcaster.push({"kind": "done", "report": report})
        return report


@app.get("/invoices/classify/result")
def last_result():
    """预留：扫描结果的读取口，等台账（need.md 五）落地后再实现"""
    raise HTTPException(status_code=501, detail="尚未实现")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    broadcaster.loop = asyncio.get_running_loop()
    broadcaster.clients.add(ws)
    try:
        while True:
            # 界面目前只收不发，收到的都当心跳丢弃，顺带探连接还在不在
            await ws.receive_text()
    except WebSocketDisconnect:
        broadcaster.clients.discard(ws)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=DEFAULT_HOST, port=DEFAULT_PORT)
