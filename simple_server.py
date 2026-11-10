#!/usr/bin/env python3
"""
简单的测试服务器
"""

from http.server import HTTPServer, SimpleHTTPRequestHandler
import os

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory="/data/data/com.termux/files/home/.hermes/red-pen", **kwargs)

if __name__ == "__main__":
    server = HTTPServer(('0.0.0.0', 8080), Handler)
    print("🚀 启动简单HTTP服务器...")
    print("📡 访问地址: http://localhost:8080")
    server.serve_forever()