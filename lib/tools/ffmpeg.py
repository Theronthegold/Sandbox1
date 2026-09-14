"""
ffmpeg.py

ffmpeg / ffprobe 바이너리 탐색과 실행 공통 유틸.

탐색 순서:
  1. 환경변수 FFMPEG_DIR (bin 폴더 경로)
  2. PATH
  3. winget 설치 경로 (Gyan.FFmpeg) - Windows 에서 새 셸을 열기 전에도 동작하게
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional


class FFmpegError(RuntimeError):
    pass


def find_binary(name: str = "ffmpeg") -> str:
    exe = f"{name}.exe" if os.name == "nt" else name

    env_dir = os.environ.get("FFMPEG_DIR")
    if env_dir and (Path(env_dir) / exe).exists():
        return str(Path(env_dir) / exe)

    found = shutil.which(name)
    if found:
        return found

    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        pattern = os.path.join(local, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", exe)
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]

    raise FFmpegError(
        f"{name} 을 찾을 수 없습니다. `winget install Gyan.FFmpeg` 로 설치하거나 FFMPEG_DIR 를 설정하세요."
    )


def run(args: List[str], *, binary: str = "ffmpeg", timeout: Optional[float] = 600) -> subprocess.CompletedProcess:
    """ffmpeg/ffprobe 실행. 실패 시 stderr 마지막 부분을 담아 FFmpegError."""
    cmd = [find_binary(binary), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise FFmpegError(f"{binary} 실패 (exit {proc.returncode}):\n{tail}")
    return proc


def probe_duration(path: os.PathLike | str) -> float:
    """미디어 파일 길이(초)."""
    proc = run(
        ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        binary="ffprobe",
        timeout=60,
    )
    return float(proc.stdout.strip())


def escape_filter_path(path: os.PathLike | str) -> str:
    """filter 인자(ass=, subtitles=)에 들어가는 경로 이스케이프. Windows 드라이브 콜론 처리."""
    p = str(Path(path).resolve()).replace("\\", "/")
    return p.replace(":", "\\:").replace("'", "\\'")
