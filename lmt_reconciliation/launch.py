"""Start the app locally and open it in the browser (used by the launchers)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

PORT = int(os.environ.get("LMT_RECON_PORT", "8501"))
URL = f"http://localhost:{PORT}"


def running() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/_stcore/health", timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    if running():
        print(f"The tool is already running. Opening {URL}")
        webbrowser.open(URL)
        return 0
    # headless=true: no first-run email prompt in the terminal; we open the browser ourselves.
    proc = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "app.py", "--server.headless", "true",
                             "--server.address", "localhost", "--server.port", str(PORT),
                             "--browser.gatherUsageStats", "false"], cwd=here)
    for _ in range(120):
        if running():
            break
        if proc.poll() is not None:
            print("The tool stopped while starting. See the messages above.")
            return 1
        time.sleep(0.5)
    print(f"\nLMT CRM Reconciliation is running at {URL}\nClose this window (or press Ctrl+C) to stop it.\n")
    if not os.environ.get("LMT_RECON_NO_BROWSER"):
        webbrowser.open(URL)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    sys.exit(main())
