"""
producer.py

director 산출물을 받아 도구만으로 영상을 만듭니다 (LLM 호출 없음).

  TTS(씬별) → 스톡 클립 검색/다운로드 → ASS 자막 → 음악 선택 → ffmpeg 렌더 → QA 프레임 추출

PexelsClient 가 없으면(키 미설정) 개발용 단색 클립을 생성해 파이프라인을 끝까지 돌릴 수 있습니다.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Set

from lib.tools import music, render, subtitles, tts
from lib.tools.ffmpeg import run as ffmpeg_run
from lib.tools.stock import PexelsClient, StockError

from .schemas import DirectorOutput

logger = logging.getLogger("orchestrator.producer")

EventSink = Callable[[str, str], "asyncio.Future | None"]  # (agent, message) -> awaitable or None


@dataclass
class ProduceResult:
    video_path: Path
    duration: float
    frame_paths: List[Path]
    scene_durations: List[float]
    clip_sources: List[str] = field(default_factory=list)   # Pexels page URL 또는 "placeholder"
    music_path: Optional[Path] = None


async def produce(
    directed: DirectorOutput,
    work_dir: Path,
    *,
    stock: Optional[PexelsClient] = None,
    voice: str = tts.DEFAULT_VOICE,
    on_event: Optional[Callable[[str], "asyncio.Future | None"]] = None,
) -> ProduceResult:
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    async def emit(msg: str) -> None:
        logger.info(msg)
        if on_event:
            r = on_event(msg)
            if asyncio.iscoroutine(r) or isinstance(r, asyncio.Future):
                await r

    scenes = directed.scenes
    n = len(scenes)

    # 1) TTS
    await emit(f"TTS 합성 시작 ({n} 씬, voice={voice})")
    tts_results = await tts.synthesize_scenes([s.narration for s in scenes], work_dir / "tts", voice=voice)
    scene_durations = [r.duration for r in tts_results]
    await emit(f"TTS 완료: 총 {sum(scene_durations):.1f}초")

    # 2) 스톡 클립
    clip_paths: List[Path] = []
    clip_sources: List[str] = []
    used_ids: Set[int] = set()
    for i, s in enumerate(scenes):
        if stock is None:
            p = _placeholder_clip(work_dir / "clips" / f"placeholder_{i:02d}.mp4", i, scene_durations[i])
            clip_paths.append(p)
            clip_sources.append("placeholder")
            continue
        try:
            clip, path = await asyncio.to_thread(
                stock.fetch, s.stock_query, min_duration=max(2.0, scene_durations[i] * 0.6), exclude_ids=used_ids
            )
            used_ids.add(clip.id)
            clip_paths.append(path)
            clip_sources.append(clip.page_url)
            await emit(f"씬 {i}: '{s.stock_query}' → pexels {clip.id} ({clip.duration:.0f}s)")
        except StockError as e:
            # 검색 실패 시 더 일반적인 검색어로 1회 재시도, 그래도 없으면 placeholder
            fallback_q = " ".join(s.stock_query.split()[:2]) or "city"
            await emit(f"씬 {i}: '{s.stock_query}' 실패 ({e}). '{fallback_q}' 로 재시도")
            try:
                clip, path = await asyncio.to_thread(stock.fetch, fallback_q, min_duration=2.0, exclude_ids=used_ids)
                used_ids.add(clip.id)
                clip_paths.append(path)
                clip_sources.append(clip.page_url)
            except StockError:
                clip_paths.append(_placeholder_clip(work_dir / "clips" / f"placeholder_{i:02d}.mp4", i, scene_durations[i]))
                clip_sources.append("placeholder")
    await emit("스톡 클립 준비 완료")

    # 3) 자막
    abs_words = tts.absolute_words(tts_results)
    ass_text = subtitles.build_ass([
        subtitles.SceneWords(words=w, emphasis=s.emphasis, preset=s.preset) for w, s in zip(abs_words, scenes)
    ])
    ass_path = subtitles.write_ass(ass_text, work_dir / "subs.ass")

    # 4) 음악
    music_path = music.pick_track(directed.mood)
    await emit(f"음악: {music_path.name if music_path else '없음'} (mood={directed.mood})")

    # 5) 렌더
    await emit("렌더링 시작")
    video_path = await asyncio.to_thread(
        render.render_short,
        [render.SceneRender(clip_path=c, duration=d) for c, d in zip(clip_paths, scene_durations)],
        [r.audio_path for r in tts_results],
        work_dir / "short.mp4",
        ass_path=ass_path,
        music_path=music_path,
    )
    duration = sum(scene_durations)
    await emit(f"렌더 완료: {video_path.name} ({duration:.1f}s)")

    # 6) QA 프레임: 씬마다 중간 지점 1장 (최대 8장)
    frames: List[Path] = []
    t = 0.0
    step = max(1, n // 8)
    for i, d in enumerate(scene_durations):
        if i % step == 0 and len(frames) < 8:
            frames.append(await asyncio.to_thread(
                render.extract_frame, video_path, t + d / 2, work_dir / "frames" / f"scene_{i:02d}.jpg"
            ))
        t += d

    return ProduceResult(
        video_path=video_path,
        duration=duration,
        frame_paths=frames,
        scene_durations=scene_durations,
        clip_sources=clip_sources,
        music_path=music_path,
    )


_PLACEHOLDER_COLORS = ["0x1f2a44", "0x2d1f44", "0x1f4436", "0x44301f", "0x3a1f1f", "0x1f3a44"]


def _placeholder_clip(path: Path, index: int, duration: float) -> Path:
    """Pexels 키가 없을 때 쓰는 단색 클립 (개발/테스트 용)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    color = _PLACEHOLDER_COLORS[index % len(_PLACEHOLDER_COLORS)]
    ffmpeg_run([
        "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:size=1080x1920:rate=30",
        "-t", f"{max(1.0, duration) + 0.5:.2f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ])
    return path
