"""Continuous runner for completing the 3 livebooks in Semester 5 without stopping:
1. Computer Organization and Architecture
2. DevOps Foundations
3. Introduction to Philosophy
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

LIVEBOOKS = [
    "Computer Organization and Architecture",
    "DevOps Foundations",
    "Introduction to Philosophy",
]


def log(msg: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def run_livebook(lb_name: str) -> int:
    log(f"==================================================")
    log(f"STARTING LIVEBOOK: {lb_name}")
    log(f"==================================================")
    repo_root = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, "-u", "main.py", "--livebook", lb_name]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(repo_root),
    )
    for line in iter(proc.stdout.readline, ""):
        line_str = line.rstrip()
        if line_str:
            print(f"[{lb_name[:15]}] {line_str}", flush=True)
    proc.stdout.close()
    return_code = proc.wait()
    log(f"Finished {lb_name} with return code: {return_code}")
    return return_code


def main():
    log("Continuous Automation Manager starting...")
    results = {}
    for lb in LIVEBOOKS:
        rc = run_livebook(lb)
        results[lb] = rc
        time.sleep(5)

    log("==================================================")
    log("ALL 3 LIVEBOOKS PROCESSED!")
    for lb, rc in results.items():
        log(f"  - {lb}: Exit code {rc}")
    log("==================================================")


if __name__ == "__main__":
    main()
