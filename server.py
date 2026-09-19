#!/usr/bin/env python3
"""
server.py — jev-llm 网页版后端。纯标准库,无第三方依赖。

GET  /           聊天页面
POST /api/chat   SSE 流:请求体 {"history": [{role, text}, ...]},
                 逐词推送 jev_chat.stream_reply() 的事件。
                 API key 只在服务端使用,不会下发给浏览器。

启动: python3 server.py  →  http://127.0.0.1:8137
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jev_chat import load_env, stream_reply

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 8137
API_KEY = None
HISTORY_TURNS = 20   # 最多带最近这么多轮对话, 防止 state 无限膨胀

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "jev-llm/1.0"

    def log_message(self, fmt, *args):
        pass  # 静默访问日志

    def _static(self):
        name, ctype = STATIC.get(self.path, (None, None))
        if name is None:
            self.send_error(404)
            return
        try:
            with open(os.path.join(ROOT, name), "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in STATIC:
            self._static()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/api/chat":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return

        history = [
            {"role": str(m.get("role")), "text": str(m.get("text", ""))[:2000]}
            for m in body.get("history", [])[-HISTORY_TURNS:]
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
        ]

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        usage = {}
        try:
            for ev in stream_reply(API_KEY, history, usage):
                self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端断开, 直接收摊


def main():
    global API_KEY
    load_env(os.path.join(ROOT, ".env"))
    API_KEY = os.environ.get("API_KEY")
    if not API_KEY:
        raise SystemExit("缺少 API_KEY(请在 .env 里配置)")

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    httpd.daemon_threads = True
    print(f"jev-llm 已启动: http://127.0.0.1:{PORT}  (Ctrl-C 退出)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
