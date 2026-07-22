"""Minimal Chrome DevTools Protocol client (stdlib only) for preview inspection.

Used to read browser console / page errors and confirm a preview URL loads,
without adding Playwright or selenium dependencies.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import subprocess
import tempfile
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

logger = logging.getLogger(__name__)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


class _WsClient:
    """Tiny RFC6455 client sufficient for local Chromium CDP."""

    def __init__(self, host: str, port: int, path: str, *, timeout: float = 20.0) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(req.encode("ascii"))
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("CDP websocket handshake failed")
            header += chunk
        status_line = header.split(b"\r\n", 1)[0].decode("latin1", errors="replace")
        if "101" not in status_line:
            raise ConnectionError(f"CDP handshake rejected: {status_line}")

    def send_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])  # text, FIN
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header.extend(n.to_bytes(2, "big"))
        else:
            header.append(0x80 | 127)
            header.extend(n.to_bytes(8, "big"))
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self._sock.sendall(header + masked)

    def recv_json(self, *, timeout: float | None = None) -> dict[str, Any] | None:
        if timeout is not None:
            self._sock.settimeout(timeout)
        try:
            b0 = _recv_exact(self._sock, 2)
        except (TimeoutError, socket.timeout, BlockingIOError, OSError):
            return None
        opcode = b0[0] & 0x0F
        length = b0[1] & 0x7F
        if length == 126:
            length = int.from_bytes(_recv_exact(self._sock, 2), "big")
        elif length == 127:
            length = int.from_bytes(_recv_exact(self._sock, 8), "big")
        masked = bool(b0[1] & 0x80)
        mask = _recv_exact(self._sock, 4) if masked else b""
        payload = _recv_exact(self._sock, length)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:  # close
            return None
        if opcode == 0x9:  # ping → pong
            # Echo pong
            frame = bytearray([0x8A, len(payload)]) + payload
            try:
                self._sock.sendall(frame)
            except OSError:
                pass
            return self.recv_json(timeout=timeout)
        if opcode != 0x1:
            return {}
        try:
            data = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_devtools(port: int, *, timeout: float = 15.0) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and data:
                return data
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_err = exc
            time.sleep(0.15)
    raise TimeoutError(f"Chromium DevTools not ready on :{port}: {last_err}")


def _console_text(args: list[Any]) -> str:
    parts: list[str] = []
    for arg in args or []:
        if not isinstance(arg, dict):
            parts.append(str(arg))
            continue
        value = arg.get("value")
        if value is not None:
            parts.append(str(value))
            continue
        desc = arg.get("description") or arg.get("unserializableValue")
        if desc:
            parts.append(str(desc))
            continue
        parts.append(str(arg.get("type") or "object"))
    text = " ".join(parts).strip()
    return text[:2000]


def inspect_url_with_devtools(
    url: str,
    *,
    browser: str,
    width: int = 1280,
    height: int = 800,
    timeout_s: float = 25.0,
    include_screenshot: bool = False,
    settle_s: float = 1.2,
) -> dict[str, Any]:
    """Load ``url`` in headless Chromium and return console/page diagnostics.

    Returns keys: ok, load_ok, title, ready_state, console_logs, page_errors,
    network_failures, screenshot (optional).
    """
    port = _pick_free_port()
    profile = tempfile.mkdtemp(prefix="syte-cdp-")
    proc: subprocess.Popen[bytes] | None = None
    ws: _WsClient | None = None
    console_logs: list[dict[str, Any]] = []
    page_errors: list[dict[str, Any]] = []
    network_failures: list[dict[str, Any]] = []
    load_ok = False
    title = ""
    ready_state = ""
    screenshot_b64 = ""
    try:
        cmd = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--remote-allow-origins=*",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            f"--window-size={int(width)},{int(height)}",
            "about:blank",
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        targets = _wait_for_devtools(port, timeout=min(12.0, timeout_s))
        page = next(
            (t for t in targets if str(t.get("type") or "") == "page" and t.get("webSocketDebuggerUrl")),
            targets[0],
        )
        ws_url = str(page.get("webSocketDebuggerUrl") or "")
        # ws://127.0.0.1:PORT/devtools/page/ID
        if not ws_url.startswith("ws://"):
            raise ConnectionError(f"Unexpected CDP websocket URL: {ws_url}")
        host_port, _, path = ws_url.removeprefix("ws://").partition("/")
        host, _, port_s = host_port.partition(":")
        ws = _WsClient(host or "127.0.0.1", int(port_s or port), "/" + path, timeout=timeout_s)

        next_id = 1

        def call(method: str, params: dict[str, Any] | None = None) -> int:
            nonlocal next_id
            msg_id = next_id
            next_id += 1
            payload: dict[str, Any] = {"id": msg_id, "method": method}
            if params:
                payload["params"] = params
            assert ws is not None
            ws.send_json(payload)
            return msg_id

        call("Runtime.enable")
        call("Page.enable")
        call("Network.enable")
        call("Log.enable")
        nav_id = call("Page.navigate", {"url": url})

        deadline = time.monotonic() + timeout_s
        navigated = False
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            event = ws.recv_json(timeout=min(1.0, remaining))
            if event is None:
                continue
            if event.get("id") == nav_id:
                result = event.get("result") or {}
                err = event.get("error")
                if err:
                    page_errors.append({
                        "type": "navigate_error",
                        "text": str(err.get("message") or err)[:1000],
                    })
                elif result.get("errorText"):
                    page_errors.append({
                        "type": "navigate_error",
                        "text": str(result.get("errorText"))[:1000],
                    })
                else:
                    navigated = True
                continue
            method = str(event.get("method") or "")
            params = event.get("params") or {}
            if method == "Runtime.consoleAPICalled":
                level = str(params.get("type") or "log")
                text = _console_text(params.get("args") or [])
                if text:
                    console_logs.append({
                        "level": level,
                        "text": text,
                        "timestamp": params.get("timestamp"),
                    })
            elif method == "Log.entryAdded":
                entry = params.get("entry") or {}
                text = str(entry.get("text") or "").strip()
                if text:
                    console_logs.append({
                        "level": str(entry.get("level") or "info"),
                        "text": text[:2000],
                        "source": entry.get("source"),
                        "url": entry.get("url"),
                    })
            elif method == "Runtime.exceptionThrown":
                details = (params.get("exceptionDetails") or {})
                text = str(
                    details.get("text")
                    or (details.get("exception") or {}).get("description")
                    or "exception"
                )
                page_errors.append({
                    "type": "exception",
                    "text": text[:2000],
                    "url": details.get("url"),
                    "line": details.get("lineNumber"),
                })
            elif method == "Network.loadingFailed":
                network_failures.append({
                    "url": str(params.get("errorText") or "")[:200],
                    "type": str(params.get("type") or ""),
                    "canceled": bool(params.get("canceled")),
                    "error": str(params.get("errorText") or "")[:500],
                    "requestId": params.get("requestId"),
                })
            elif method in {"Page.loadEventFired", "Page.domContentEventFired"}:
                load_ok = True
                if method == "Page.loadEventFired":
                    break

        if navigated and not load_ok:
            # Soft success: navigation started even if load event was slow.
            load_ok = not page_errors

        # Let late console noise settle briefly.
        settle_deadline = time.monotonic() + max(0.2, settle_s)
        while time.monotonic() < settle_deadline:
            event = ws.recv_json(timeout=0.2)
            if not event:
                continue
            method = str(event.get("method") or "")
            params = event.get("params") or {}
            if method == "Runtime.consoleAPICalled":
                text = _console_text(params.get("args") or [])
                if text:
                    console_logs.append({
                        "level": str(params.get("type") or "log"),
                        "text": text,
                    })
            elif method == "Runtime.exceptionThrown":
                details = params.get("exceptionDetails") or {}
                text = str(
                    details.get("text")
                    or (details.get("exception") or {}).get("description")
                    or "exception"
                )
                page_errors.append({"type": "exception", "text": text[:2000]})

        eval_id = call(
            "Runtime.evaluate",
            {
                "expression": "({title: document.title || '', readyState: document.readyState || '', href: location.href || ''})",
                "returnByValue": True,
            },
        )
        eval_deadline = time.monotonic() + 3.0
        while time.monotonic() < eval_deadline:
            event = ws.recv_json(timeout=0.5)
            if not event:
                continue
            if event.get("id") == eval_id:
                value = ((event.get("result") or {}).get("result") or {}).get("value") or {}
                if isinstance(value, dict):
                    title = str(value.get("title") or "")[:300]
                    ready_state = str(value.get("readyState") or "")
                break

        if include_screenshot:
            shot_id = call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
            shot_deadline = time.monotonic() + 5.0
            while time.monotonic() < shot_deadline:
                event = ws.recv_json(timeout=0.5)
                if not event:
                    continue
                if event.get("id") == shot_id:
                    screenshot_b64 = str((event.get("result") or {}).get("data") or "")
                    break

        # Cap lists for LLM context.
        console_logs = console_logs[-80:]
        page_errors = page_errors[-40:]
        network_failures = [
            item for item in network_failures[-40:]
            if not item.get("canceled")
        ]

        severe = [
            item for item in console_logs
            if str(item.get("level") or "").lower() in {"error", "assert"}
        ]
        ok = bool(load_ok) and not page_errors and not severe
        message = (
            f"Preview loaded ({ready_state or 'unknown'})"
            if load_ok
            else "Preview did not fire load event"
        )
        if severe or page_errors:
            message += f" — {len(severe)} console error(s), {len(page_errors)} page error(s)"
        if network_failures:
            message += f", {len(network_failures)} network failure(s)"

        result: dict[str, Any] = {
            "ok": ok,
            "load_ok": load_ok,
            "url": url,
            "title": title,
            "ready_state": ready_state,
            "console_logs": console_logs,
            "page_errors": page_errors,
            "network_failures": network_failures,
            "console_error_count": len(severe),
            "page_error_count": len(page_errors),
            "message": message,
            "width": width,
            "height": height,
        }
        if include_screenshot and screenshot_b64:
            try:
                png = base64.b64decode(screenshot_b64)
            except Exception:
                png = b""
            if png.startswith(b"\x89PNG\r\n\x1a\n"):
                result["format"] = "png"
                result["image_base64"] = screenshot_b64
                result["png_bytes"] = png
                result["bytes"] = len(png)
        return result
    except Exception as exc:
        logger.debug("CDP inspect failed for %s", url, exc_info=True)
        return {
            "ok": False,
            "load_ok": False,
            "url": url,
            "title": title,
            "ready_state": ready_state,
            "console_logs": console_logs,
            "page_errors": page_errors + [{"type": "cdp_error", "text": str(exc)[:1000]}],
            "network_failures": network_failures,
            "console_error_count": sum(
                1 for item in console_logs
                if str(item.get("level") or "").lower() in {"error", "assert"}
            ),
            "page_error_count": len(page_errors) + 1,
            "message": f"Browser DevTools inspect failed: {exc}",
            "error": "devtools_failed",
            "width": width,
            "height": height,
        }
    finally:
        if ws is not None:
            ws.close()
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            import shutil

            shutil.rmtree(profile, ignore_errors=True)
        except Exception:
            pass
