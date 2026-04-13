#!/usr/bin/env python3
"""Run all sync jobs in parallel. Single entry point for the scheduled workflow."""

import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

JOBS = [
    ("costumes", [sys.executable, "apps/costumes/sync.py"]),
    ("excuses", [sys.executable, "apps/excuses/sync.py"]),
    ("screenplay", [sys.executable, "apps/screenplay/sync.py"]),
    ("scripts", [sys.executable, "apps/scripts/sync.py"]),
    ("finance", [sys.executable, "apps/finance/report.py"]),
]


def run_job(name, cmd):
    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - start
    return name, result.returncode, elapsed, result.stdout, result.stderr


def main():
    selected = None
    if len(sys.argv) > 1:
        selected = set(sys.argv[1].split(","))

    jobs = [(n, c) for n, c in JOBS if selected is None or n in selected]
    print(f"Running {len(jobs)} jobs: {', '.join(n for n, _ in jobs)}\n")

    results = {}
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {pool.submit(run_job, name, cmd): name for name, cmd in jobs}
        for future in as_completed(futures):
            name, code, elapsed, stdout, stderr = future.result()
            results[name] = code
            status = "OK" if code == 0 else f"FAIL ({code})"
            print(f"[{status}] {name} ({elapsed:.1f}s)")
            if stdout.strip():
                for line in stdout.strip().splitlines():
                    print(f"  {line}")
            if stderr.strip() and code != 0:
                for line in stderr.strip().splitlines():
                    print(f"  ! {line}")
            print()

    failed = [n for n, c in results.items() if c != 0]
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        sys.exit(1)
    print("All jobs completed successfully.")


if __name__ == "__main__":
    main()
