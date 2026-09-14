"""
music.py

배경음악 선택. Pixabay Music 은 공식 API 가 없어서, 처음에 트랙을 받아
assets/music/ 에 두고 파일명 앞에 분위기 태그를 붙이는 방식으로 씁니다.

    assets/music/calm_morning-light.mp3
    assets/music/upbeat_city-run.mp3
    assets/music/tense_deep-focus.mp3

에이전트는 mood 만 고르고, 파일 선택은 여기서 규칙으로 합니다.
트랙이 하나도 없으면 None 을 돌려주고 render 는 음악 없이 진행합니다.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

DEFAULT_MUSIC_DIR = Path("assets/music")
MOODS = ("calm", "upbeat", "tense", "inspiring")
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".ogg"}


def pick_track(mood: Optional[str] = None, *, music_dir: Path = DEFAULT_MUSIC_DIR, seed: Optional[int] = None) -> Optional[Path]:
    music_dir = Path(music_dir)
    if not music_dir.is_dir():
        return None
    tracks = [p for p in music_dir.iterdir() if p.suffix.lower() in AUDIO_EXT]
    if not tracks:
        return None

    if mood:
        matched = [p for p in tracks if p.name.lower().startswith(mood.lower() + "_")]
        if matched:
            tracks = matched

    rng = random.Random(seed)
    return rng.choice(sorted(tracks))
