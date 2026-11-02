#!/usr/bin/env python3
"""Monthly update for the simple site (run on your own computer, once a month).

  python data/pipeline/monthly_update.py

1. Harvests arXiv records added or updated since the last run (OAI-PMH, no key needed).
2. Rebuilds the site data for the most recent 60 months (~500k papers) into public/research/data
   (AIRG_MONTHS changes the window).
3. Research directions: embeds only the new papers' abstracts (GPU if available,
   see embed_gpu.py), assigns every paper to its directions and recomputes trends.
   The directions themselves stay fixed; run `directions.py build --refit` to redo them.
Takes roughly 10-20 minutes depending on the network.
"""
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(*args):
    print(">", " ".join(args), flush=True)
    subprocess.run([sys.executable, *args], cwd=HERE, check=True)


def main():
    today = date.today()
    end = today.replace(day=1) - timedelta(days=1)          # last day of the previous month
    months = int(os.environ.get("AIRG_MONTHS", "60"))      # full months in the window
    y, m = end.year, end.month - months + 1
    while m < 1:
        y, m = y - 1, m + 12
    start = date(y, m, 1)
    run("harvest_arxiv.py", "--until", today.isoformat())
    run("build_simple.py", "all", "--start", start.isoformat(), "--end", end.isoformat())
    run("directions.py", "export")
    run("embed_gpu.py")
    run("directions.py", "build")
    print(f"done: site data for {start} .. {end} is in public/research/data")


if __name__ == "__main__":
    main()
