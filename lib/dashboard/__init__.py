"""
lib.dashboard

FastAPI 관리 대시보드. Job 생성/승인/재시도, 단계 진행과 에이전트 상태, 비용, 이벤트 로그(SSE), 영상 미리보기.

    python -m scripts.serve            # http://127.0.0.1:8000
"""

from .app import create_app

__all__ = ["create_app"]
