"""
tts.py

edge-tts (Microsoft Edge 온라인 음성) 로 한국어 나레이션 합성.
API 키 불필요, 무료. 단어별 타임스탬프(WordBoundary)를 함께 반환해서
자막 싱크에 바로 씁니다.

씬 단위로 따로 합성하는 것을 전제로 합니다. 그래야 씬 길이 = 그 씬 오디오 길이가 되어
스톡 클립 트리밍과 자막 타이밍이 단순해집니다.

주의: 공식 API 가 아니라 Edge 브라우저 내부 서비스입니다. 막히면 Qwen3-TTS /
CosyVoice2 같은 로컬 모델로 이 파일만 교체하면 됩니다 (인터페이스 유지).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Sequence

import edge_tts

from .ffmpeg import probe_duration

KO_VOICES = {
    "male": "ko-KR-InJoonNeural",
    "male_multi": "ko-KR-HyunsuMultilingualNeural",
    "female": "ko-KR-SunHiNeural",
}
DEFAULT_VOICE = KO_VOICES["male"]

_TICKS_PER_SEC = 10_000_000  # edge-tts offset/duration 단위 (100ns)


@dataclass
class WordTiming:
    text: str
    start: float  # seconds
    end: float    # seconds


@dataclass
class TTSResult:
    audio_path: Path
    duration: float
    words: List[WordTiming] = field(default_factory=list)


async def synthesize(
    text: str,
    out_path: Path,
    *,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",     # 예: "+10%" 빠르게, "-5%" 느리게
    pitch: str = "+0Hz",
) -> TTSResult:
    """문장 하나를 mp3 로 합성하고 단어 타임스탬프를 돌려줍니다."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tts = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    words: List[WordTiming] = []
    with open(out_path, "wb") as f:
        async for chunk in tts.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / _TICKS_PER_SEC
                words.append(WordTiming(
                    text=chunk["text"],
                    start=start,
                    end=start + chunk["duration"] / _TICKS_PER_SEC,
                ))

    return TTSResult(audio_path=out_path, duration=probe_duration(out_path), words=words)


async def synthesize_scenes(
    texts: Sequence[str],
    out_dir: Path,
    *,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
) -> List[TTSResult]:
    """씬별 나레이션을 순서대로 합성. 파일명은 scene_00.mp3, scene_01.mp3 ..."""
    out_dir = Path(out_dir)
    results = []
    for i, text in enumerate(texts):
        results.append(await synthesize(text, out_dir / f"scene_{i:02d}.mp3", voice=voice, rate=rate))
    return results


def absolute_words(results: Sequence[TTSResult]) -> List[List[WordTiming]]:
    """씬별 상대 타임스탬프를 전체 영상 기준 절대 시각으로 변환.

    씬 i 의 시작 = 앞선 씬들의 duration 합. render.py 가 오디오를 같은 순서로
    이어붙이므로 이 계산과 정확히 일치합니다.
    """
    out: List[List[WordTiming]] = []
    offset = 0.0
    for r in results:
        out.append([WordTiming(w.text, w.start + offset, w.end + offset) for w in r.words])
        offset += r.duration
    return out
