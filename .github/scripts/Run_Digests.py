#!/usr/bin/env python3
"""
Run Pipeline_Digest.py, then Raw_Digest.py, then Push_to_CODA.py back-to-back
for one or more dates — for catching up manually when the scheduled
workflows didn't fire.
"""
import subprocess
import sys
from pathlib import Path

DATES = ["08-06-2026"]

SCRIPT_DIR = Path(__file__).resolve().parent


def run_step(*args):
    print(f"\n$ {' '.join(args)}")
    result = subprocess.run([sys.executable, *args])
    if result.returncode != 0:
        sys.exit(f"ERROR: {' '.join(args)} failed (exit {result.returncode}); stopping.")


def main():
    if not DATES:
        sys.exit("DATES is empty — add MM-DD-YYYY dates to the DATES list at the top of this script.")

    for date in DATES:
        print(f"\n=== {date} ===")
        run_step(str(SCRIPT_DIR / "Pipeline_Digest.py"), "--to", date)
        run_step(str(SCRIPT_DIR / "Raw_Digest.py"), "--to", date)
        run_step(str(SCRIPT_DIR / "Push_to_CODA.py"))

    print("\nDone with all dates.")


if __name__ == "__main__":
    main()
