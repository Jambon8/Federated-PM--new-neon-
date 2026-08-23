#!/usr/bin/env python3
"""Capture the web UI screenshots the README shows.

The script starts the Flask app, drives a headless Chromium through the DevTools
protocol, and writes one PNG per shot next to this file. Every shot comes from a
real run of the protocol on the logs under `data/2parties/`, so re-running the
script after a UI change refreshes the images instead of leaving them stale.

    python3 web/screenshots/capture.py                  # every shot
    python3 web/screenshots/capture.py --only summary    # one shot
    python3 web/screenshots/capture.py --list            # names and what they show

Requires a Chromium-family browser on the path and MP-SPDZ installed under
`vendor/temp/`. Log paths are entered relative to the repository root, which is
also what the captured "Configuration as executed" block then echoes.
"""

import argparse
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))

APP_PORT = 8000
DEVTOOLS_PORT = 9333
VIEWPORT = (1440, 1000)
BROWSERS = ("google-chrome", "chromium", "chromium-browser", "chrome", "brave-browser")

# One dataset per shot, chosen for what it demonstrates rather than for size.
DEMO = "data/2parties/bpi13_open"
HANDOVER = "data/2parties/requestforpayment"
CONCURRENT = "data/2parties/sepsis"


# --------------------------------------------------------------------- devtools

class _Socket:
    """The subset of RFC 6455 a DevTools session needs, over the standard library."""

    def __init__(self, url, timeout=30):
        parsed = urllib.parse.urlparse(url)
        self.socket = socket.create_connection((parsed.hostname, parsed.port), timeout)
        path = parsed.path or "/"
        key = base64.b64encode(os.urandom(16)).decode()
        self.socket.sendall((
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        self.buffer = b""
        while b"\r\n\r\n" not in self.buffer:
            self.buffer += self._some()
        head, _, self.buffer = self.buffer.partition(b"\r\n\r\n")
        status = head.split(b"\r\n")[0]
        if b" 101 " not in status:
            raise RuntimeError(f"DevTools refused the upgrade: {status!r}")

    def _some(self):
        chunk = self.socket.recv(1 << 16)
        if not chunk:
            raise ConnectionError("DevTools closed the connection")
        return chunk

    def _exact(self, count):
        while len(self.buffer) < count:
            self.buffer += self._some()
        head, self.buffer = self.buffer[:count], self.buffer[count:]
        return head

    def _frame(self, opcode, payload=b""):
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.socket.sendall(bytes(header) + masked)

    def send(self, text):
        self._frame(0x1, text.encode())

    def receive(self):
        """One complete text message, reassembled across fragments."""
        parts = []
        while True:
            first, second = self._exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._exact(8))[0]
            payload = self._exact(length) if length else b""
            if opcode == 0x8:
                raise ConnectionError("DevTools closed the connection")
            if opcode == 0x9:            # ping
                self._frame(0xA, payload)
                continue
            if opcode == 0xA:            # pong
                continue
            parts.append(payload)
            if first & 0x80:
                return b"".join(parts).decode()

    def close(self):
        try:
            self._frame(0x8)
        except OSError:
            pass
        self.socket.close()


class Page:
    """A DevTools page target: evaluate expressions and capture pixels."""

    def __init__(self, websocket_url):
        self.connection = _Socket(websocket_url)
        self.counter = 0
        self.call("Page.enable")
        self.call("Runtime.enable")
        self.call("Emulation.setDeviceMetricsOverride",
                  width=VIEWPORT[0], height=VIEWPORT[1],
                  deviceScaleFactor=1, mobile=False)

    def call(self, method, **params):
        self.counter += 1
        self.connection.send(json.dumps({"id": self.counter, "method": method,
                                         "params": params}))
        while True:
            message = json.loads(self.connection.receive())
            if message.get("id") != self.counter:
                continue                       # an event, not our reply
            if "error" in message:
                raise RuntimeError(f"{method}: {message['error'].get('message')}")
            return message.get("result", {})

    def evaluate(self, expression):
        outcome = self.call("Runtime.evaluate", expression=f"(() => {{{expression}}})()",
                            returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in outcome:
            raise RuntimeError(outcome["exceptionDetails"].get("text", "page exception"))
        return outcome.get("result", {}).get("value")

    def navigate(self, url):
        self.call("Page.navigate", url=url)
        self.await_true("return document.readyState === 'complete'", 30)

    def await_true(self, expression, seconds, interval=0.4):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.evaluate(expression):
                return
            time.sleep(interval)
        raise TimeoutError(f"still false after {seconds}s: {expression}")

    def resize(self, height):
        self.call("Emulation.setDeviceMetricsOverride", width=VIEWPORT[0],
                  height=int(height), deviceScaleFactor=1, mobile=False)

    def capture(self, path, selector=None, padding=12, max_height=None):
        if selector:
            # A panel inside a scrolling column is taller than its own box, so
            # the emulated viewport grows to the content before the clip is cut.
            box = self.evaluate(f"""
                const node = document.querySelector({selector!r});
                if (!node) return null;
                const r = node.getBoundingClientRect();
                return {{x: r.left + scrollX, y: r.top + scrollY, width: r.width,
                         height: Math.max(r.height, node.scrollHeight)}};
            """)
            if not box:
                raise RuntimeError(f"no element matches {selector}")
            self.resize(max(VIEWPORT[1], box["y"] + box["height"] + 2 * padding))
            box = self.evaluate(f"""
                const node = document.querySelector({selector!r});
                const r = node.getBoundingClientRect();
                return {{x: r.left + scrollX, y: r.top + scrollY, width: r.width,
                         height: Math.max(r.height, node.scrollHeight)}};
            """)
            height = box["height"] + 2 * padding
            clip = {"x": max(0, box["x"] - padding), "y": max(0, box["y"] - padding),
                    "width": box["width"] + 2 * padding,
                    "height": min(height, max_height) if max_height else height,
                    "scale": 1}
        else:
            size = self.call("Page.getLayoutMetrics")
            content = size.get("cssContentSize") or size["contentSize"]
            clip = {"x": 0, "y": 0, "width": content["width"],
                    "height": content["height"], "scale": 1}
        shot = self.call("Page.captureScreenshot", format="png", clip=clip,
                         captureBeyondViewport=True)
        with open(path, "wb") as stream:
            stream.write(base64.b64decode(shot["data"]))
        self.resize(VIEWPORT[1])
        return os.path.getsize(path)

    def close(self):
        self.connection.close()


# ----------------------------------------------------------------- the page API

def fill(page, values):
    """Set form controls by element id and fire the events the page listens for."""
    page.evaluate(f"""
        const values = {json.dumps(values)};
        for (const [id, value] of Object.entries(values)) {{
            const node = document.getElementById(id);
            if (!node) throw new Error('no control #' + id);
            if (node.type === 'checkbox') node.checked = Boolean(value);
            else node.value = String(value);
            node.dispatchEvent(new Event('input', {{bubbles: true}}));
            node.dispatchEvent(new Event('change', {{bubbles: true}}));
        }}
        return true;
    """)


def execute(page, minutes=30):
    """Click Run and wait for the summary to render."""
    page.evaluate("document.getElementById('runBtn').click(); return true;")
    page.await_true("return document.getElementById('runBtn').disabled === true", 30)
    page.await_true("return document.getElementById('runBtn').disabled === false",
                    minutes * 60, interval=2)
    page.await_true("""
        const tile = document.querySelector('#tilesRow .tile .v');
        return Boolean(tile) && tile.textContent.trim() !== '\\u2014';
    """, 60)


RUNS = os.path.join(HERE, ".runs")


def protocol_run(name, flags, reuse=False):
    """Run the protocol once and return its output, the way /api/run does.

    A run that takes an hour must not be held open in a browser tab: the page
    accumulates every log line, and a lost renderer loses the result. The run
    happens here instead, and the page renders its output through /api/decode.
    """
    os.makedirs(RUNS, exist_ok=True)
    cached = os.path.join(RUNS, f"{name}.log")
    if reuse and os.path.exists(cached):
        print(f"    reusing {os.path.relpath(cached, ROOT)}")
        with open(cached, encoding="utf-8") as stream:
            return stream.read()
    command = [sys.executable, "-u", "pipeline/run.py", *flags]
    print("    " + " ".join(command[2:]))
    finished = subprocess.run(command, cwd=ROOT, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if finished.returncode != 0:
        raise RuntimeError(f"{name}: the protocol exited {finished.returncode}")
    with open(cached, "w", encoding="utf-8") as stream:
        stream.write(finished.stdout)
    return finished.stdout


def render(page, output):
    """Show a finished run in the page, through the parser the UI itself uses."""
    page.evaluate("""
        LAST_RAW = %s;
        document.getElementById('rawOutput').textContent = LAST_RAW;
        return true;
    """ % json.dumps(output))
    page.evaluate("""
        return fetch('/api/decode', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({raw: LAST_RAW, reveal_from: []})
        }).then(response => response.json()).then(parsed => {
            if (parsed.error) throw new Error(parsed.error);
            renderResults(parsed);
            return true;
        });
    """)
    page.await_true("""
        const tile = document.querySelector('#tilesRow .tile .v');
        return Boolean(tile) && tile.textContent.trim() !== '\u2014';
    """, 60)


def expand_config(page):
    page.evaluate("""
        const block = [...document.querySelectorAll('details')]
            .find(node => node.innerText.includes('Configuration as executed'));
        if (block) block.open = true;
        return true;
    """)


# ----------------------------------------------------------------------- shots

def shot_summary(page, base):
    """The whole tool after one run: configuration, release, and stage costs."""
    page.navigate(base)
    fill(page, {"log_0": f"{DEMO}/party_0.xes.gz",
                "log_1": f"{DEMO}/party_1.xes.gz",
                "threshold": 5})
    execute(page)
    expand_config(page)
    return page.capture(os.path.join(HERE, "run_summary.png"))


def shot_options(page, base):
    """The optional features, each with its calibrated parameters."""
    page.navigate(base)
    fill(page, {"log_0": f"{DEMO}/party_0.xes.gz",
                "log_1": f"{DEMO}/party_1.xes.gz",
                "use_handovers": True,
                "handover_activities": f"{HANDOVER}/handover.txt",
                "partial_orders": True, "delta_value": 10, "delta_unit": "s",
                "enable_dp": True})
    # The cutoff arrives from /api/dp-preview, so the panel reads "Calibrating"
    # for a moment after the checkbox goes on.
    page.await_true("""
        return document.getElementById('dp_preview').textContent.includes('k =');
    """, 30)
    return page.capture(os.path.join(HERE, "optional_features.png"), "aside.rail",
                        padding=3)


def shot_partial_orders(page, base):
    """Concurrent steps in the release, from a log whose events share timestamps.

    Sepsis records simultaneous laboratory tests, so every variant it releases
    above the threshold carries a concurrent step. Its width also makes this the
    longest compile of the set, which is why the output is cached.
    """
    output = protocol_run("partial_orders", [
        "--logs", f"{CONCURRENT}/party_0.xes.gz", f"{CONCURRENT}/party_1.xes.gz",
        "--threshold", "5", "--threads", "16",
        "--partial-orders", "1", "--delta", "0",
    ], reuse=True)
    page.navigate(base)
    fill(page, {"log_0": f"{CONCURRENT}/party_0.xes.gz",
                "log_1": f"{CONCURRENT}/party_1.xes.gz",
                "threshold": 5,
                "partial_orders": True, "delta_value": 0, "delta_unit": "s"})
    render(page, output)
    counts = page.evaluate("""
        const rows = [...document.querySelectorAll('#resultsTableBody tr')];
        return {rows: rows.length,
                concurrent: rows.filter(row => row.innerText.includes('[')).length};
    """)
    print(f"    {counts['concurrent']} of {counts['rows']} released variants "
          "carry a concurrent step")
    return page.capture(os.path.join(HERE, "partial_orders.png"), "#tracesCard",
                        max_height=760)


def shot_handover(page, base):
    """Opaque fingerprints, then the same release with one party's runs revealed.

    Never cached: revealing reads the reversal tables the last run left in
    Player-Data, so the run behind the image has to be the most recent one.
    """
    output = protocol_run("handover", [
        "--logs", f"{HANDOVER}/party_0.xes.gz", f"{HANDOVER}/party_1.xes.gz",
        "--threshold", "5", "--threads", "16",
        "--use-handovers", "--handover-activities", f"{HANDOVER}/handover.txt",
    ])
    page.navigate(base)
    fill(page, {"log_0": f"{HANDOVER}/party_0.xes.gz",
                "log_1": f"{HANDOVER}/party_1.xes.gz",
                "threshold": 5, "use_handovers": True,
                "handover_activities": f"{HANDOVER}/handover.txt"})
    render(page, output)
    written = page.capture(os.path.join(HERE, "handover_fingerprints.png"),
                           "#tracesCard", max_height=760)
    page.evaluate("""
        const box = document.querySelector('#revealParties input');
        if (!box) throw new Error('the release carries no fingerprints to reveal');
        box.checked = true;
        box.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    """)
    page.await_true("""
        return !document.getElementById('revealNote').textContent.includes('\\u2026');
    """, 60)
    written += page.capture(os.path.join(HERE, "handover_revealed.png"),
                            "#tracesCard", max_height=760)
    return written


SHOTS = {
    "summary": (shot_summary, "the whole tool after one run"),
    "options": (shot_options, "the optional features and their parameters"),
    "orders": (shot_partial_orders, "concurrent steps under a partial order"),
    "handover": (shot_handover, "fingerprints, hidden and revealed"),
}


# ----------------------------------------------------------------- orchestration

def wait_for(url, seconds, description):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.3)
    raise TimeoutError(f"{description} did not answer at {url} within {seconds}s")


def start_app():
    if is_listening(APP_PORT):
        raise SystemExit(f"Port {APP_PORT} is busy. Stop the running web UI first.")
    process = subprocess.Popen([sys.executable, "web/app.py"], cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for(f"http://localhost:{APP_PORT}/api/config", 30, "the web UI")
    return process


def start_browser(profile):
    binary = next((shutil.which(name) for name in BROWSERS if shutil.which(name)), None)
    if not binary:
        raise SystemExit("No Chromium-family browser on the path: " + ", ".join(BROWSERS))
    process = subprocess.Popen([
        binary, "--headless=new", f"--remote-debugging-port={DEVTOOLS_PORT}",
        f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
        "--disable-extensions", "--hide-scrollbars",
        f"--window-size={VIEWPORT[0]},{VIEWPORT[1]}", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for(f"http://localhost:{DEVTOOLS_PORT}/json/version", 30, "the browser")
    targets = wait_for(f"http://localhost:{DEVTOOLS_PORT}/json/list", 15, "the browser")
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise SystemExit("The browser exposed no page target")
    return process, os.path.basename(binary), pages[0]["webSocketDebuggerUrl"]


def stop(process):
    """End a child we started, whatever state it reached."""
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def is_listening(port):
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", action="append", choices=sorted(SHOTS),
                        help="Capture just these shots (repeatable).")
    parser.add_argument("--list", action="store_true", help="Name the shots and exit.")
    arguments = parser.parse_args()

    if arguments.list:
        for name, (_, description) in sorted(SHOTS.items()):
            print(f"  {name:10s} {description}")
        return 0

    wanted = arguments.only or sorted(SHOTS)
    profile = os.path.join(HERE, ".chrome-profile")
    app = browser = page = None
    try:
        app = start_app()
        browser, binary, websocket_url = start_browser(profile)
        print(f"web UI on port {APP_PORT}, {binary} on port {DEVTOOLS_PORT}")
        page = Page(websocket_url)
        for name in wanted:
            capture, description = SHOTS[name]
            print(f"  {name}: {description}")
            started = time.monotonic()
            written = capture(page, f"http://localhost:{APP_PORT}/")
            print(f"    {written / 1024:.0f} KB in {time.monotonic() - started:.0f}s")
    finally:
        if page:
            try:
                page.close()
            except OSError:
                pass
        for process in (browser, app):
            stop(process)
        shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
