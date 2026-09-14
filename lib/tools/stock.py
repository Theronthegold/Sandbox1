"""
stock.py

Pexels Video API 로 세로(9:16) 실사 스톡 클립 검색/다운로드.
무료, 상업 이용 가능, 출처 표기 불필요. 환경변수 PEXELS_API_KEY 필요
(https://www.pexels.com/api/ 에서 무료 발급).

다운로드한 파일은 cache_dir 에 클립 ID 로 저장되어 재렌더링 때 다시 받지 않습니다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Set

import httpx

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
DEFAULT_CACHE_DIR = Path("assets/stock")


@dataclass
class StockClip:
    id: int
    url: str            # 다운로드할 mp4 링크
    width: int
    height: int
    duration: float
    page_url: str       # 출처 (기록용)
    photographer: str


class StockError(RuntimeError):
    pass


class PexelsClient:
    def __init__(self, api_key: Optional[str] = None, cache_dir: Path = DEFAULT_CACHE_DIR, timeout: float = 60.0):
        key = api_key or os.environ.get("PEXELS_API_KEY")
        if not key:
            raise StockError("PEXELS_API_KEY 환경변수가 없습니다. https://www.pexels.com/api/ 에서 무료 발급하세요.")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = httpx.Client(headers={"Authorization": key}, timeout=timeout, follow_redirects=True)

    # ---- 검색 ----

    def search_portrait(self, query: str, *, per_page: int = 10, min_duration: float = 3.0) -> List[StockClip]:
        """세로 클립만 검색. min_duration 보다 짧은 클립은 제외."""
        resp = self._http.get(
            PEXELS_SEARCH_URL,
            params={"query": query, "orientation": "portrait", "size": "medium", "per_page": per_page},
        )
        if resp.status_code == 429:
            raise StockError("Pexels rate limit (200 req/hour). 잠시 후 다시 시도하세요.")
        resp.raise_for_status()

        clips: List[StockClip] = []
        for v in resp.json().get("videos", []):
            if v.get("duration", 0) < min_duration:
                continue
            file = _pick_file(v.get("video_files", []))
            if file is None:
                continue
            clips.append(StockClip(
                id=v["id"],
                url=file["link"],
                width=file.get("width") or v.get("width", 0),
                height=file.get("height") or v.get("height", 0),
                duration=float(v["duration"]),
                page_url=v.get("url", ""),
                photographer=(v.get("user") or {}).get("name", ""),
            ))
        return clips

    # ---- 다운로드 ----

    def download(self, clip: StockClip) -> Path:
        path = self.cache_dir / f"pexels_{clip.id}.mp4"
        if path.exists() and path.stat().st_size > 0:
            return path
        tmp = path.with_suffix(".part")
        with self._http.stream("GET", clip.url) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
        tmp.replace(path)
        return path

    def fetch(self, query: str, *, min_duration: float = 3.0, exclude_ids: Iterable[int] = ()) -> tuple[StockClip, Path]:
        """검색 → 아직 안 쓴 첫 클립 선택 → 다운로드. 한 영상 안에서 같은 클립 반복을 막기 위해 exclude_ids 사용."""
        excluded: Set[int] = set(exclude_ids)
        for clip in self.search_portrait(query, min_duration=min_duration):
            if clip.id not in excluded:
                return clip, self.download(clip)
        raise StockError(f"'{query}' 로 조건에 맞는 세로 클립을 찾지 못했습니다.")

    def close(self) -> None:
        self._http.close()


def _pick_file(files: list) -> Optional[dict]:
    """세로 파일 중 1080x1920 이상을 우선, 없으면 가장 큰 세로 파일. 세로가 없으면 None."""
    portrait = [f for f in files if f.get("width") and f.get("height") and f["height"] > f["width"]]
    if not portrait:
        return None
    good = [f for f in portrait if f["height"] >= 1920]
    pool = good or portrait
    return min(pool, key=lambda f: f["height"]) if good else max(pool, key=lambda f: f["height"])
