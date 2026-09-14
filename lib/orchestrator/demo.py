"""
demo.py

API 키 없이 파이프라인과 대시보드를 끝까지 돌려보기 위한 DemoClient.
에이전트별로 실제와 비슷한 한국어 응답을 돌려주고, 진행이 보이도록 약간의 지연과
현실적인 비용(USD)을 흉내냅니다. breaker 에도 기록되지 않습니다 (실제 호출이 아니므로).

    from lib.orchestrator.demo import DemoClient
    Pipeline(store, DemoClient(topic), ...)

producer 는 실제로 동작하므로(TTS, 렌더) 결과 영상은 진짜로 만들어집니다.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from lib.model_client import ModelClient, ModelResponse

from .schemas import TopicArea

_COST = {"researcher": 0.11, "writer": 0.19, "critic": 0.07, "director": 0.06, "publisher": 0.015, "qa": 0.05}
_DELAY = {"researcher": 2.5, "writer": 3.0, "critic": 1.5, "director": 1.5, "publisher": 0.8, "qa": 1.2}

_MONEY: Dict[str, Any] = {
    "researcher": {
        "candidates": [
            {"title_idea": "월급이 통장을 스치는 이유", "hook": "월급이 통장을 스치는 이유, 아세요?",
             "angle": "현재 편향(present bias)으로 설명하는 자동 저축", "why_now": "월급날 직후 검색량 상승",
             "sources": ["https://en.wikipedia.org/wiki/Dynamic_inconsistency", "https://www.nber.org/papers/w7682"], "risk": "low"},
            {"title_idea": "세일에 속는 뇌", "hook": "세일 표시만 봐도 뇌가 켜집니다.",
             "angle": "앵커링 효과와 정가 표시", "why_now": "추석 세일 시즌",
             "sources": ["https://en.wikipedia.org/wiki/Anchoring_effect"], "risk": "low"},
            {"title_idea": "잃는 게 두 배 아픈 이유", "hook": "만 원 잃는 고통은 만 원 버는 기쁨의 두 배입니다.",
             "angle": "손실 회피와 매도 타이밍", "why_now": "증시 변동성 확대",
             "sources": ["https://en.wikipedia.org/wiki/Loss_aversion"], "risk": "medium"},
        ],
        "picked_index": 0,
        "rationale": "누구나 겪는 상황이라 공감이 빠르고, 스톡 영상(지갑, 통장, 카페)으로 표현하기 쉬우며 리스크가 낮음",
    },
    "writer": {
        "title_working": "월급이 통장을 스치는 진짜 이유",
        "scenes": [
            {"narration": "월급이 통장을 스치는 이유, 아세요?"},
            {"narration": "의지가 약해서가 아닙니다."},
            {"narration": "우리 뇌는 지금의 만족을 미래의 안정보다 훨씬 크게 느낍니다."},
            {"narration": "경제학자들은 이걸 현재 편향이라고 부릅니다."},
            {"narration": "그래서 월급이 들어오면 쓰는 게 먼저, 남으면 저축이 됩니다."},
            {"narration": "부자들은 순서를 바꿉니다."},
            {"narration": "월급날 가장 먼저 자기 자신에게 송금하는 거죠."},
            {"narration": "자동이체 하나면 의지가 필요 없어집니다."},
            {"narration": "여러분은 월급날 뭘 먼저 하세요? 댓글로 알려주세요."},
        ],
    },
    "critic": {"approve": True, "score": 84, "issues": [
        {"scene_index": 3, "kind": "clarity", "detail": "'현재 편향' 은 용어가 낯설 수 있음", "fix": "그대로 두되 다음 씬이 바로 예시를 주므로 허용"}],
        "summary": "후킹이 좋고 흐름이 자연스럽습니다. 용어 하나만 주의하면 승인 가능합니다."},
    "director": {
        "scenes": [
            {"narration": "월급이 통장을 스치는 이유, 아세요?", "stock_query": "empty wallet hands", "emphasis": ["스치는"], "preset": "big"},
            {"narration": "의지가 약해서가 아닙니다.", "stock_query": "person thinking window", "emphasis": ["아닙니다."], "preset": "pop"},
            {"narration": "우리 뇌는 지금의 만족을 미래의 안정보다 훨씬 크게 느낍니다.", "stock_query": "coffee shop dessert close up", "emphasis": ["지금의", "만족을"], "preset": "pop"},
            {"narration": "경제학자들은 이걸 현재 편향이라고 부릅니다.", "stock_query": "library books study", "emphasis": ["현재", "편향이라고"], "preset": "pop"},
            {"narration": "그래서 월급이 들어오면 쓰는 게 먼저, 남으면 저축이 됩니다.", "stock_query": "shopping bags city street", "emphasis": ["먼저,"], "preset": "pop"},
            {"narration": "부자들은 순서를 바꿉니다.", "stock_query": "businessman walking back", "emphasis": ["순서를"], "preset": "pop"},
            {"narration": "월급날 가장 먼저 자기 자신에게 송금하는 거죠.", "stock_query": "phone banking app hands", "emphasis": ["자기", "자신에게"], "preset": "pop"},
            {"narration": "자동이체 하나면 의지가 필요 없어집니다.", "stock_query": "calm morning coffee desk", "emphasis": ["자동이체"], "preset": "calm"},
            {"narration": "여러분은 월급날 뭘 먼저 하세요? 댓글로 알려주세요.", "stock_query": "city sunset skyline", "emphasis": ["댓글로"], "preset": "calm"},
        ],
        "mood": "calm",
    },
    "publisher": {
        "title": "월급이 통장을 스치는 진짜 이유 #Shorts",
        "description": "의지가 약해서가 아니라 뇌의 현재 편향 때문입니다. 월급날 자기에게 먼저 송금하는 습관, 여러분은 어떻게 하시나요?\n출처: NBER w7682\n#돈의심리학 #재테크 #행동경제학 #저축 #자동이체",
        "hashtags": ["돈의심리학", "재테크", "행동경제학", "저축", "자동이체", "월급", "소비습관"],
    },
    "qa": {"passed": True, "issues": [{"severity": "warn", "scene_index": 2, "detail": "자막이 밝은 배경과 겹치는 프레임이 있으나 외곽선으로 판독 가능"}]},
}

_AI: Dict[str, Any] = {
    "researcher": {
        "candidates": [
            {"title_idea": "AI가 스스로 코드를 고치는 법", "hook": "AI가 밤새 혼자 버그를 고쳤습니다.",
             "angle": "에이전트가 테스트를 돌리며 반복 수정하는 구조를 비유로 설명", "why_now": "코딩 에이전트 출시 경쟁",
             "sources": ["https://www.anthropic.com/news", "https://github.blog"], "risk": "low"},
            {"title_idea": "챗봇이 거짓말하는 이유", "hook": "AI는 모르는 걸 모른다고 못 합니다.",
             "angle": "환각을 확률 예측으로 설명", "why_now": "환각 관련 뉴스 반복",
             "sources": ["https://en.wikipedia.org/wiki/Hallucination_(artificial_intelligence)"], "risk": "low"},
            {"title_idea": "1M 토큰이 뭐길래", "hook": "책 열 권을 한 번에 읽는 AI",
             "angle": "컨텍스트 창을 책상 크기에 비유", "why_now": "긴 컨텍스트 모델 보편화",
             "sources": ["https://docs.anthropic.com"], "risk": "low"},
        ],
        "picked_index": 0,
        "rationale": "시의성이 높고 '혼자 고쳤다' 는 반전 후킹이 강함. 키보드, 모니터, 야경 스톡으로 표현 가능",
    },
    "writer": {
        "title_working": "AI가 밤새 혼자 버그를 고친 방법",
        "scenes": [
            {"narration": "AI가 밤새 혼자 버그를 고쳤습니다."},
            {"narration": "사람이 시킨 건 딱 한 줄이었습니다."},
            {"narration": "테스트가 통과할 때까지 고쳐라."},
            {"narration": "AI는 코드를 고치고, 테스트를 돌리고, 실패 메시지를 읽습니다."},
            {"narration": "그리고 다시 고칩니다."},
            {"narration": "이 반복을 사람 대신 수백 번 하는 게 코딩 에이전트입니다."},
            {"narration": "핵심은 똑똑함이 아니라 지치지 않는 반복입니다."},
            {"narration": "여러분 일에서 이런 반복은 뭐가 있나요? 댓글로 알려주세요."},
        ],
    },
    "critic": {"approve": True, "score": 81, "issues": [
        {"scene_index": 5, "kind": "fact", "detail": "'수백 번' 은 과장일 수 있음", "fix": "'수십 번, 수백 번' 처럼 범위로 완화"}],
        "summary": "구조가 좋습니다. 5번 씬의 숫자 표현만 범위로 바꾸면 승인입니다."},
    "director": {
        "scenes": [
            {"narration": "AI가 밤새 혼자 버그를 고쳤습니다.", "stock_query": "dark office monitor glow", "emphasis": ["혼자"], "preset": "big"},
            {"narration": "사람이 시킨 건 딱 한 줄이었습니다.", "stock_query": "typing keyboard close up", "emphasis": ["딱", "한"], "preset": "pop"},
            {"narration": "테스트가 통과할 때까지 고쳐라.", "stock_query": "terminal code screen", "emphasis": ["통과할"], "preset": "pop"},
            {"narration": "AI는 코드를 고치고, 테스트를 돌리고, 실패 메시지를 읽습니다.", "stock_query": "programmer screen reflection", "emphasis": ["실패"], "preset": "pop"},
            {"narration": "그리고 다시 고칩니다.", "stock_query": "hands keyboard night", "emphasis": ["다시"], "preset": "pop"},
            {"narration": "이 반복을 사람 대신 수백 번 하는 게 코딩 에이전트입니다.", "stock_query": "server room lights", "emphasis": ["반복을"], "preset": "pop"},
            {"narration": "핵심은 똑똑함이 아니라 지치지 않는 반복입니다.", "stock_query": "city night timelapse", "emphasis": ["지치지", "않는"], "preset": "calm"},
            {"narration": "여러분 일에서 이런 반복은 뭐가 있나요? 댓글로 알려주세요.", "stock_query": "sunrise city skyline", "emphasis": ["댓글로"], "preset": "calm"},
        ],
        "mood": "upbeat",
    },
    "publisher": {
        "title": "AI가 밤새 혼자 버그를 고친 방법 #Shorts",
        "description": "코딩 에이전트는 고치고, 테스트하고, 다시 고치는 반복을 사람 대신 합니다. 여러분 일의 반복은 무엇인가요?\n#AI #인공지능 #코딩에이전트 #개발자 #테크",
        "hashtags": ["AI", "인공지능", "코딩에이전트", "개발자", "테크", "자동화"],
    },
    "qa": {"passed": True, "issues": []},
}

_CANNED = {TopicArea.money_psychology: _MONEY, TopicArea.ai_tech: _AI}


class DemoClient(ModelClient):
    """실제 API 대신 준비된 응답을 돌려주는 클라이언트. 주제별 내용이 다릅니다."""

    def __init__(self, topic: TopicArea = TopicArea.money_psychology, *, delay_scale: float = 1.0):
        self.topic = topic
        self.delay_scale = delay_scale
        self.calls: List[Dict[str, Any]] = []

    @property
    def model(self) -> str:
        return "demo"

    async def call(self, agent_name: str, messages, *, system: Optional[str] = None,
                   max_tokens: Optional[int] = None, **extra: Any) -> ModelResponse:
        self.calls.append({"agent": agent_name, "extra": extra})
        await asyncio.sleep(_DELAY.get(agent_name, 1.0) * self.delay_scale)
        payload = _CANNED[self.topic][agent_name]
        text = json.dumps(payload, ensure_ascii=False)
        cost = _COST.get(agent_name, 0.05)
        return ModelResponse(
            text=text, model="demo", stop_reason="end_turn",
            input_tokens=int(cost * 50_000), output_tokens=int(cost * 8_000), cost_usd=cost,
            raw=SimpleNamespace(content=[SimpleNamespace(type="text", text=text)]),
        )
