"""
subtitles.py

단어 타임스탬프 → ASS 자막 (libass 애니메이션 태그 사용).

효과:
  - 2~3 단어씩 한 줄로 묶어 화면 중하단에 표시
  - 줄이 나타날 때 살짝 커지는 팝(pop) + 페이드
  - 말하는 단어가 실시간으로 밝게 바뀜 (karaoke \\k)
  - 강조 단어는 다른 색 (노란색)

스타일은 STYLE_PRESETS 에 코드로 고정하고, 에이전트는 프리셋 이름만 고릅니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence

from .tts import WordTiming

# ASS 색상은 &HAABBGGRR& (알파, 파랑, 초록, 빨강 순)
WHITE = "&H00FFFFFF&"
GREY = "&H00B0B0B0&"
YELLOW = "&H0000D7FF&"
CYAN = "&H00FFD700&"
BLACK = "&H00000000&"


@dataclass(frozen=True)
class StylePreset:
    font: str = "Malgun Gothic"
    size: int = 84
    primary: str = WHITE       # 말한 뒤 색
    secondary: str = GREY      # 말하기 전 색
    emphasis: str = YELLOW     # 강조 단어 색
    outline: int = 6
    shadow: int = 2
    pos_y: int = 1380          # 1920 기준 세로 위치 (중앙 아래)
    max_words: int = 3
    pop: bool = True


STYLE_PRESETS: Dict[str, StylePreset] = {
    "pop": StylePreset(),
    "calm": StylePreset(size=72, pop=False, emphasis=CYAN, pos_y=1450),
    "big": StylePreset(size=100, max_words=2, pos_y=960),
}

WIDTH, HEIGHT = 1080, 1920


@dataclass
class SceneWords:
    words: List[WordTiming]                      # 절대 시각 기준 (tts.absolute_words 출력)
    emphasis: List[str] = field(default_factory=list)
    preset: str = "pop"


def build_ass(scenes: Sequence[SceneWords]) -> str:
    header = _header()
    events = []
    for scene in scenes:
        preset = STYLE_PRESETS.get(scene.preset, STYLE_PRESETS["pop"])
        emph = {_norm(e) for e in scene.emphasis}
        for line in _chunk(scene.words, preset.max_words):
            events.append(_dialogue(line, preset, emph))
    return header + "\n".join(events) + "\n"


def write_ass(text: str, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")  # BOM 있어야 일부 libass 빌드에서 한글 안전
    return path


# ---------------------------------------------------------------------------

def _header() -> str:
    p = STYLE_PRESETS["pop"]
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {WIDTH}\nPlayResY: {HEIGHT}\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{p.font},{p.size},{p.primary},{p.secondary},{BLACK},&H80000000&,"
        f"-1,0,0,0,100,100,0,0,1,{p.outline},{p.shadow},5,40,40,40,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _chunk(words: Sequence[WordTiming], n: int) -> List[List[WordTiming]]:
    return [list(words[i:i + n]) for i in range(0, len(words), n)]


def _dialogue(line: List[WordTiming], p: StylePreset, emph: set) -> str:
    start = line[0].start
    end = line[-1].end + 0.25

    tags = f"\\an5\\pos({WIDTH // 2},{p.pos_y})\\fad(60,80)"
    tags += f"\\fs{p.size}\\1c{p.primary}\\2c{p.secondary}\\bord{p.outline}\\shad{p.shadow}\\fn{p.font}"
    if p.pop:
        tags += "\\fscx88\\fscy88\\t(0,110,\\fscx100\\fscy100)"

    parts = []
    cursor = start
    for w in line:
        # 앞 단어와의 공백 시간을 이 단어의 karaoke 시간에 포함시켜 하이라이트가 자연스럽게 이어지게
        k_cs = max(1, round((w.end - cursor) * 100))
        cursor = w.end
        color = p.emphasis if _norm(w.text) in emph else p.primary
        parts.append(f"{{\\k{k_cs}\\1c{color}}}{_escape(w.text)}")

    text = "{" + tags + "}" + " ".join(parts)
    return f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{text}"


def _ts(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def _norm(s: str) -> str:
    return s.strip().strip(".,!?…\"'").lower()
