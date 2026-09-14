"""
에이전트 6개. 각 파일은 이름/출력 모델/effort/도구만 선언하고 나머지는 base_agent 가 처리합니다.

  researcher  주제 조사, 후보 선정          (web_search 도구)
  writer      대본 작성                     (effort high)
  critic      대본 검수
  director    씬 연출: 스톡 검색어, 강조, 프리셋, 음악 mood
  publisher   제목/설명/해시태그            (effort low)
  qa          렌더 결과 프레임 검수          (비전)
"""

from typing import Any, Dict, List

from lib.orchestrator.schemas import (
    CriticOutput,
    DirectorOutput,
    PublishOutput,
    QAOutput,
    ResearchOutput,
    Script,
)

from .base_agent import Agent, AgentOutputError

# 웹 검색 서버 도구. 모델이 지원하지 않는 타입이면 400 이 나므로 여기서만 바꾸면 됩니다.
WEB_SEARCH_TOOL: Dict[str, Any] = {"type": "web_search_20260209", "name": "web_search", "max_uses": 6}


class Researcher(Agent[ResearchOutput]):
    name = "researcher"
    output_model = ResearchOutput
    effort = "medium"
    max_tokens = 12000
    tools: List[Dict[str, Any]] = [WEB_SEARCH_TOOL]


class Writer(Agent[Script]):
    name = "writer"
    output_model = Script
    effort = "high"


class Critic(Agent[CriticOutput]):
    name = "critic"
    output_model = CriticOutput
    effort = "medium"


class Director(Agent[DirectorOutput]):
    name = "director"
    output_model = DirectorOutput
    effort = "medium"


class Publisher(Agent[PublishOutput]):
    name = "publisher"
    output_model = PublishOutput
    effort = "low"
    max_tokens = 2000


class QA(Agent[QAOutput]):
    name = "qa"
    output_model = QAOutput
    effort = "medium"


__all__ = ["Agent", "AgentOutputError", "Researcher", "Writer", "Critic", "Director", "Publisher", "QA", "WEB_SEARCH_TOOL"]
