from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser


def _find_browser() -> list[str] | None:
    candidates = [
        ("msedge.exe", []),
        ("chrome.exe", []),
        ("firefox.exe", []),
    ]
    for exe, extra in candidates:
        path = shutil.which(exe)
        if path:
            return [path, *extra]
    return None


def _wait_for_server(url: str, timeout_s: float = 20.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _terminate_process(proc: subprocess.Popen[bytes] | None, force: bool = True) -> None:
    if proc is None or proc.poll() is not None:
        return
    if force:
        try:
            proc.kill()
            return
        except Exception:
            pass
    try:
        proc.terminate()
        proc.wait(timeout=2.0)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Zero Cycle Dashboard and close browser on Ctrl+C.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", default=True)
    parser.add_argument("--reload", dest="reload", action="store_true")
    parser.add_argument("--no-reload", dest="reload", action="store_false")
    parser.set_defaults(reload=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    url = f"http://{args.host}:{args.port}"
    health_url = f"{url}/api/health"

    uvicorn_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--timeout-graceful-shutdown",
        "0",
    ]
    if args.reload:
        uvicorn_cmd.extend(["--reload", "--reload-dir", "app", "--reload-dir", "scripts"])

    uvicorn_proc = subprocess.Popen(uvicorn_cmd, cwd=root)
    browser_proc: subprocess.Popen[bytes] | None = None

    def _shutdown(_: int | None = None, __: object | None = None) -> None:
        _terminate_process(browser_proc, force=True)
        _terminate_process(uvicorn_proc, force=True)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        if not args.no_browser and _wait_for_server(health_url):
            browser_cmd = _find_browser()
            if browser_cmd:
                browser_proc = subprocess.Popen([*browser_cmd, url], cwd=root)
            else:
                webbrowser.open(url)

        return uvicorn_proc.wait()
    except KeyboardInterrupt:
        _shutdown()
        return 130
    finally:
        _shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
