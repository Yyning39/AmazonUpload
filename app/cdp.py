"""连接本机 Chrome 的调试端口。不打开网页，不点击页面。

参考原项目 amazon_automation.py 的 _WebSocket、ChromePage 和 AmazonClient.connect。
这里不按国家网址挑选标签，也不在连接失败时启动 Chrome。
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import urlparse


LOCAL_HOSTS = {"127.0.0.1", "localhost"}


class BrowserConnectionError(ConnectionError):
    """连接本机 Chrome 失败。"""


def list_targets(port, host="127.0.0.1"):
    """GET http://127.0.0.1:<端口>/json/list，返回标签列表。

    参考原项目 AmazonClient.targets（amazon_automation.py）。
    """
    _require_local_host(host)
    url = f"http://127.0.0.1:{int(port)}/json/list"
    try:
        with urlopen(url, timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError, TimeoutError) as exc:
        raise BrowserConnectionError(f"无法读取 Chrome 标签列表：{exc}") from exc
    if not isinstance(data, list):
        raise BrowserConnectionError("Chrome 标签列表格式不正确")
    return data


def choose_target(targets):
    """从标签列表中选一个可控制的网页，返回含 webSocketDebuggerUrl 的项。

    参考原项目 AmazonClient.connect 对 type=page 的筛选。
    不按 Seller Central 路径挑选；有普通 http 页面时不用 chrome:// 页。
    """
    pages = []
    for target in targets or []:
        if target.get("type") != "page":
            continue
        websocket_url = str(target.get("webSocketDebuggerUrl") or "")
        if not _is_local_websocket(websocket_url):
            continue
        pages.append(target)
    if not pages:
        raise BrowserConnectionError("Chrome 没有可控制的页面")
    web_pages = [target for target in pages if str(target.get("url") or "").startswith("http")]
    return (web_pages or pages)[0]


def connect_port(port, host="127.0.0.1", timeout=20):
    """连接调试端口。失败时每 0.5 秒重试，超时抛错。

    重试间隔参考原项目 AmazonClient.open_or_connect。
    本函数不启动 Chrome。
    """
    _require_local_host(host)
    deadline = time.monotonic() + float(timeout)
    last_error = None
    while True:
        session = None
        try:
            target = choose_target(list_targets(port, host))
            session = PageSession(target)
            session.confirm_control()
            return session
        except Exception as exc:
            last_error = exc
            if session is not None:
                session.close()
            if time.monotonic() >= deadline:
                raise BrowserConnectionError(
                    f"连接 Chrome 超时（127.0.0.1:{int(port)}）：{last_error}"
                ) from last_error
            time.sleep(0.5)


class PageSession:
    """一条已通过可控性检查的标签页连接。"""

    def __init__(self, target, timeout=15):
        self.target_id = target.get("id")
        self.page_url = target.get("url") or ""
        self.websocket_url = target["webSocketDebuggerUrl"]
        self._socket = DevToolsSocket(self.websocket_url, timeout=timeout)
        self._seq = 0

    def call(self, method, params=None):
        """发送一条 CDP 命令并返回 result。参考 ChromePage.call。"""
        self._seq += 1
        return self._socket.call({"id": self._seq, "method": method, "params": params or {}})

    def confirm_control(self):
        """发四条命令确认这条连接可以控制页面。参考 AmazonClient.connect。"""
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("DOM.enable")
        self.call("DOM.getDocument", {"depth": 1})

    def evaluate(self, expression):
        """执行页面脚本并返回值。"""
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        if result.get("exceptionDetails"):
            raise BrowserConnectionError("页面脚本执行失败")
        return result.get("result", {}).get("value")

    def set_file_input_files(self, expression, path):
        """把本地绝对路径交给 expression 返回的 file input。

        参考原项目 ChromePage.set_file_target：
        Runtime.evaluate 取得节点，DOM.requestNode 取 nodeId，
        再 DOM.setFileInputFiles。不点击文件框。
        """
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": False, "userGesture": True},
        )
        if result.get("exceptionDetails"):
            raise BrowserConnectionError("页面脚本执行失败")
        object_id = (result.get("result") or {}).get("objectId")
        if not object_id:
            raise BrowserConnectionError("没有拿到文件框节点")
        self.call("DOM.getDocument", {"depth": 1})
        node_id = self.call("DOM.requestNode", {"objectId": object_id}).get("nodeId")
        if not node_id:
            raise BrowserConnectionError("没有拿到文件框节点编号")
        self.call("DOM.setFileInputFiles", {"nodeId": node_id, "files": [str(path)]})

    def close(self):
        self._socket.close()


class DevToolsSocket:
    """只连接 127.0.0.1 或 localhost 的 CDP WebSocket。参考原项目 _WebSocket。"""

    def __init__(self, url, timeout=15):
        parsed = urlparse(url)
        if not _is_local_websocket(url):
            raise BrowserConnectionError("Chrome 返回了不安全的调试地址")
        self._sock = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout)
        self._sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = parsed.path or "/"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            part = self._sock.recv(4096)
            if not part:
                raise BrowserConnectionError("Chrome 调试连接被关闭")
            response += part
            if len(response) > 16384:
                raise BrowserConnectionError("Chrome 调试握手响应异常")
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise BrowserConnectionError("Chrome 未接受远程调试连接")

    def call(self, payload):
        self._send(json.dumps(payload).encode("utf-8"))
        while True:
            data = json.loads(self._recv().decode("utf-8"))
            if data.get("id") == payload["id"]:
                if "error" in data:
                    raise BrowserConnectionError(str(data["error"]))
                return data.get("result", {})
            # 事件帧先跳过。这一层不处理弹窗或网络事件。

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass

    def _send(self, payload: bytes):
        mask = os.urandom(4)
        size = len(payload)
        if size < 126:
            header = bytes([0x81, 0x80 | size])
        elif size <= 0xFFFF:
            header = bytes([0x81, 0x80 | 126]) + struct.pack(">H", size)
        else:
            header = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", size)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._sock.sendall(header + mask + masked)

    def _recv(self) -> bytes:
        def exact(count):
            out = b""
            while len(out) < count:
                part = self._sock.recv(count - len(out))
                if not part:
                    raise BrowserConnectionError("Chrome 调试连接中断")
                out += part
            return out

        first, second = exact(2)
        opcode, masked, length = first & 0x0F, bool(second & 0x80), second & 0x7F
        if length == 126:
            length = struct.unpack(">H", exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", exact(8))[0]
        mask = exact(4) if masked else b""
        data = exact(length)
        if masked:
            data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        if opcode == 0x9:
            self._send(data)
            return self._recv()
        if opcode == 0x8:
            raise BrowserConnectionError("Chrome 调试连接已关闭")
        return data


def _require_local_host(host):
    if str(host or "").strip().lower() not in LOCAL_HOSTS:
        raise BrowserConnectionError("只连接本机 127.0.0.1 或 localhost")


def _is_local_websocket(url):
    parsed = urlparse(str(url or ""))
    return parsed.scheme in {"ws", "wss"} and (parsed.hostname or "").lower() in LOCAL_HOSTS
