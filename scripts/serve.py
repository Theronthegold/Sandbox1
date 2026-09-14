"""
serve.py - 대시보드 서버 실행

    python -m scripts.serve                 # http://127.0.0.1:8000
    python -m scripts.serve --port 8080 --budget 2.0
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from lib.dashboard import create_app


def main(argv=None) -> None:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", default=8000, type=int)
    p.add_argument("--db", default=Path("output/orchestrator.db"), type=Path)
    p.add_argument("--budget", default=1.5, type=float, help="편당 예산 USD")
    args = p.parse_args(argv)

    app = create_app(db_path=args.db, per_job_budget_usd=args.budget)
    print(f"dashboard: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
