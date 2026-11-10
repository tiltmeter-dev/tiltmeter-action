"""A local API for the action's end-to-end run.

``start`` serves a customer on localhost and returns once it answers; the server
outlives the step that started it. ``drift`` renames a field of the customer, as a
provider would.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

PORT = 8765
SERVED = Path(__file__).parent / "served"
URL = f"http://127.0.0.1:{PORT}/customer.json"
BEFORE = {"id": 1, "phone": "555-0100", "plan": "pro"}
AFTER = {"id": 1, "phone_number": "555-0100", "plan": "pro"}


def write(body: dict[str, Any]) -> None:
    SERVED.mkdir(exist_ok=True)
    (SERVED / "customer.json").write_text(json.dumps(body), encoding="utf-8")


def start() -> None:
    write(BEFORE)
    detached: dict[str, Any] = (
        {
            "creationflags": subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
        }
        if os.name == "nt"
        else {"start_new_session": True}
    )
    server = [sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1"]
    subprocess.Popen(
        [*server, "--directory", str(SERVED)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detached,
    )
    problem: object = "no answer"
    for _ in range(150):
        try:
            with urllib.request.urlopen(URL, timeout=1) as response:
                if response.status == 200:
                    print(f"Serving {URL}")
                    return
        except OSError as exc:
            problem = exc
            time.sleep(0.2)
    raise SystemExit(f"{URL} did not answer: {problem}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "start":
        start()
    elif command == "drift":
        write(AFTER)
        print("The API now returns phone_number instead of phone.")
    else:
        raise SystemExit("usage: api.py start|drift")
