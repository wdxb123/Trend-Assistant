#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最小 NDJSON 流式示例 —— 与 trendradar/assistant/web.py 同一套路：
  服务端：每行一个 JSON（UTF-8），末尾 \\n，及时 flush
  浏览器：fetch → ReadableStream → TextDecoder → 按 \\n 切行 → JSON.parse

运行：
  python examples/minimal_ndjson_stream.py
浏览器打开：
  http://127.0.0.1:8766/
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


PAGE_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>minimal NDJSON stream</title>
  <style>
    body { font-family: sans-serif; max-width: 640px; margin: 24px auto; }
    #log {
      white-space: pre-wrap; word-break: break-word;
      border: 1px solid #ccc; padding: 12px; min-height: 120px;
    }
    button { padding: 8px 16px; cursor: pointer; }
  </style>
</head>
<body>
  <p><button id="btn">拉一条模拟流式响应</button></p>
  <div id="log"></div>
  <script>
    async function runStream() {
      const log = document.getElementById('log');
      log.textContent = '';

      const res = await fetch('/stream', { method: 'POST' });
      const reader = res.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // 最后一截可能是不完整 JSON，放回 buffer

        for (const line of lines) {
          const t = line.trim();
          if (!t) continue;
          const evt = JSON.parse(t);
          if (evt.type === 'delta') log.textContent += evt.chunk || '';
          if (evt.type === 'done') {
            log.textContent += String.fromCharCode(10, 10) + '[done，全文长度 ' + String((evt.full || '').length) + ']';
          }
        }
      }
    }

    document.getElementById('btn').addEventListener('click', () => {
      runStream().catch((e) => {
        document.getElementById('log').textContent = String(e);
      });
    });
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "MinimalNDJSON/0.1"

    def log_message(self, format: str, *args) -> None:
        pass

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            raw = PAGE_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/stream":
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        text = "NDJSON 一行一个 JSON；delta 拼界面；done 收尾。\n"

        def write(obj: dict) -> None:
            line = json.dumps(obj, ensure_ascii=False) + "\n"
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()

        # 模拟：字符逐个 delta（真实场景里是模型 tokenizer 输出的一段段文本）
        for ch in text:
            write({"type": "delta", "chunk": ch})
            time.sleep(0.02)

        write({"type": "done", "full": text.strip()})


def main() -> None:
    host = "127.0.0.1"
    port = 8766
    httpd = HTTPServer((host, port), Handler)
    print(f"打开浏览器: http://{host}:{port}/")
    print("Ctrl+C 结束")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
