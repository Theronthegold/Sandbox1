"""
render.py

ffmpeg 한 번 실행으로 최종 쇼츠 mp4 를 만듭니다.

입력:
  - 씬별 스톡 클립 + 씬 길이 (= 그 씬 나레이션 길이)
  - 씬별 나레이션 mp3 (같은 순서)
  - ASS 자막 파일 (선택)
  - 배경음악 (선택)

처리:
  - 클립을 씬 길이만큼 트리밍 (짧으면 반복), 1080x1920 으로 확대 후 중앙 크롭
  - 클립 concat → ASS 자막 번인
  - 나레이션 concat, 음악은 볼륨 낮춰 믹스, 영상 길이에 맞춰 잘라냄
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from .ffmpeg import escape_filter_path, run

WIDTH, HEIGHT, FPS = 1080, 1920, 30


@dataclass
class SceneRender:
    clip_path: Path
    duration: float


def render_short(
    scenes: Sequence[SceneRender],
    narration_paths: Sequence[Path],
    out_path: Path,
    *,
    ass_path: Optional[Path] = None,
    music_path: Optional[Path] = None,
    music_volume: float = 0.12,
    crf: int = 20,
    preset: str = "medium",
) -> Path:
    if len(scenes) != len(narration_paths):
        raise ValueError(f"씬 수({len(scenes)})와 나레이션 수({len(narration_paths)})가 다릅니다.")
    if not scenes:
        raise ValueError("씬이 없습니다.")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    args: List[str] = ["-y", "-hide_banner", "-loglevel", "error"]
    filters: List[str] = []

    # ---- 비디오 입력 (0..n-1) ----
    n = len(scenes)
    for i, s in enumerate(scenes):
        args += ["-stream_loop", "-1", "-i", str(s.clip_path)]
        filters.append(
            f"[{i}:v]trim=0:{s.duration:.3f},setpts=PTS-STARTPTS,"
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},format=yuv420p[v{i}]"
        )
    filters.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vcat]")

    if ass_path is not None:
        filters.append(f"[vcat]ass='{escape_filter_path(ass_path)}'[vout]")
    else:
        filters.append("[vcat]null[vout]")

    # ---- 나레이션 입력 (n..2n-1) ----
    for p in narration_paths:
        args += ["-i", str(p)]
    filters.append("".join(f"[{n + i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[narr]")

    # ---- 음악 입력 (2n) ----
    if music_path is not None:
        args += ["-stream_loop", "-1", "-i", str(music_path)]
        filters.append(f"[{2 * n}:a]volume={music_volume}[bgm]")
        filters.append("[narr][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]")
    else:
        filters.append("[narr]anull[aout]")

    args += [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        str(out_path),
    ]
    run(args, timeout=900)
    return out_path


def extract_frame(video_path: Path, at_sec: float, out_path: Path) -> Path:
    """QA 용 프레임 추출 (에이전트가 비전으로 확인)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run(["-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at_sec:.3f}", "-i", str(video_path),
         "-frames:v", "1", "-q:v", "2", str(out_path)], timeout=60)
    return out_path
