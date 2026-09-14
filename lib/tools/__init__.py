"""
lib.tools

LLM 이 못 하는 일을 무료 도구로 연결한 계층. 에이전트가 아니라 오케스트레이터가 직접 호출합니다.

  tts        edge-tts        한국어 나레이션 + 단어 타임스탬프
  stock      Pexels API      세로 실사 스톡 클립 (PEXELS_API_KEY 필요)
  subtitles  ASS/libass      단어 단위 텍스트 애니메이션 자막
  music      로컬 폴더        분위기 태그로 배경음악 선택
  render     ffmpeg          클립+나레이션+자막+음악 → 1080x1920 mp4
  youtube    Data API v3     업로드 (YOUTUBE_CLIENT_SECRET 필요)
"""

from . import ffmpeg, music, render, stock, subtitles, tts, youtube  # noqa: F401

__all__ = ["ffmpeg", "music", "render", "stock", "subtitles", "tts", "youtube"]
