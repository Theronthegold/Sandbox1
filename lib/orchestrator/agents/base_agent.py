"""
base_agent.py

에이전트 공통 구현.

  - 시스템 프롬프트는 prompts/<name>.md 에서 읽음 (코드와 분리)
  - 입력 pydantic 모델을 JSON 으로 직렬화해 user 메시지로 전달
  - 출력 모델의 JSON 스키마를 output_config.format 으로 강제
  - 서버 도구(web_search) 사용 시 pause_turn 재개 처리
  - 파싱 실패 시 오류 메시지를 붙여 1회 재시도

모델 호출은 전부 lib.model_client.ModelClient.call() 을 거치므로 breaker 도 자동 적용.
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, ClassVar, Dict, Generic, List, Optional, Sequence, Tuple, Type, TypeVar

from pydantic import BaseModel, ValidationError

from lib.model_client import ModelCallError, ModelClient, ModelResponse

logger = logging.getLogger("orchestrator.agent")

TOut = TypeVar("TOut", bound=BaseModel)
DEFAULT_PROMPTS_DIR = Path("prompts")


class AgentOutputError(RuntimeError):
    """모델 출력이 스키마에 맞지 않아 재시도까지 실패."""


class Agent(Generic[TOut]):
    name: ClassVar[str]
    output_model: ClassVar[Type[BaseModel]]
    effort: ClassVar[str] = "medium"
    max_tokens: ClassVar[int] = 8000
    tools: ClassVar[List[Dict[str, Any]]] = []      # 서버 도구 (예: web_search)
    max_pause_resumes: ClassVar[int] = 3

    def __init__(self, client: ModelClient, prompts_dir: Path = DEFAULT_PROMPTS_DIR):
        self.client = client
        self.prompts_dir = Path(prompts_dir)
        self._system: Optional[str] = None

    # ---- public ----

    async def run(self, payload: BaseModel, *, images: Sequence[Path] = ()) -> Tuple[TOut, float]:
        """에이전트 실행. (출력 모델, 이번 실행 비용 USD) 반환."""
        messages: List[Dict[str, Any]] = [{"role": "user", "content": self._user_content(payload, images)}]
        total_cost = 0.0

        resp, cost = await self._call_until_done(messages)
        total_cost += cost
        try:
            return self._parse(resp), total_cost
        except (ValidationError, ValueError) as e:
            logger.warning("[%s] 출력 파싱 실패, 재시도: %s", self.name, e)
            messages.append({"role": "assistant", "content": resp.text})
            messages.append({"role": "user", "content": f"출력이 스키마에 맞지 않습니다. 오류: {e}\n스키마에 맞는 JSON 만 다시 출력하세요."})
            resp, cost = await self._call_until_done(messages)
            total_cost += cost
            try:
                return self._parse(resp), total_cost
            except (ValidationError, ValueError) as e2:
                raise AgentOutputError(f"[{self.name}] 출력 파싱 2회 실패: {e2}") from e2

    # ---- internals ----

    @property
    def system_prompt(self) -> str:
        if self._system is None:
            path = self.prompts_dir / f"{self.name}.md"
            if not path.exists():
                raise FileNotFoundError(f"프롬프트 파일이 없습니다: {path}")
            self._system = path.read_text(encoding="utf-8")
        return self._system

    def _output_config(self) -> Dict[str, Any]:
        return {
            "effort": self.effort,
            "format": {"type": "json_schema", "schema": self.output_model.model_json_schema()},
        }

    async def _call_until_done(self, messages: List[Dict[str, Any]]) -> Tuple[ModelResponse, float]:
        """서버 도구가 pause_turn 을 돌려주면 assistant 내용을 붙여 이어서 호출.

        웹 검색 도구 타입을 모델이 지원하지 않아 400 이 오면 기본 타입(web_search_20250305)으로 1회 재시도.
        """
        extra: Dict[str, Any] = {"output_config": self._output_config()}
        if self.tools:
            extra["tools"] = [dict(t) for t in self.tools]

        cost = 0.0
        for _ in range(self.max_pause_resumes + 1):
            try:
                resp = await self.client.call(
                    self.name, messages, system=self.system_prompt, max_tokens=self.max_tokens, **extra
                )
            except ModelCallError as e:
                downgraded = _downgrade_web_search(extra.get("tools"), e)
                if not downgraded:
                    raise
                logger.warning("[%s] 웹 검색 도구 타입 미지원 → 기본 타입으로 재시도", self.name)
                resp = await self.client.call(
                    self.name, messages, system=self.system_prompt, max_tokens=self.max_tokens, **extra
                )
            cost += resp.cost_usd
            if resp.stop_reason != "pause_turn":
                return resp, cost
            messages.append({"role": "assistant", "content": resp.raw.content})
        return resp, cost

    def _user_content(self, payload: BaseModel, images: Sequence[Path]) -> Any:
        text = payload.model_dump_json(indent=2, exclude_none=True)
        if not images:
            return text
        blocks: List[Dict[str, Any]] = []
        for i, p in enumerate(images):
            blocks.append({"type": "text", "text": f"[프레임 {i}]"})
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg" if Path(p).suffix.lower() in (".jpg", ".jpeg") else "image/png",
                    "data": base64.standard_b64encode(Path(p).read_bytes()).decode("ascii"),
                },
            })
        blocks.append({"type": "text", "text": text})
        return blocks

    def _parse(self, resp: ModelResponse) -> TOut:
        if resp.refused:
            raise ValueError("모델이 요청을 거절했습니다 (refusal)")
        return self.output_model.model_validate_json(_extract_json(resp))  # type: ignore[return-value]


BASIC_WEB_SEARCH_TYPE = "web_search_20250305"


def _downgrade_web_search(tools: Optional[List[Dict[str, Any]]], err: ModelCallError) -> bool:
    """400 이고 원인 메시지에 web_search 가 언급되면 도구 타입을 기본형으로 바꾼다. 바꿨으면 True."""
    cause = err.__cause__
    status = getattr(cause, "status_code", None)
    if status != 400 or not tools:
        return False
    if "web_search" not in str(cause).lower():
        return False
    changed = False
    for t in tools:
        if t.get("name") == "web_search" and t.get("type") != BASIC_WEB_SEARCH_TYPE:
            t["type"] = BASIC_WEB_SEARCH_TYPE
            changed = True
    return changed


def _extract_json(resp: ModelResponse) -> str:
    """구조화 출력이면 전체 텍스트가 JSON. 서버 도구 사용 시 앞에 설명 텍스트가 섞일 수 있어 마지막 text 블록 → 중괄호 범위 순으로 시도."""
    candidates = [resp.text]
    texts = [b.text for b in resp.raw.content if getattr(b, "type", None) == "text"]
    if texts:
        candidates.append(texts[-1])
    for c in candidates:
        c = c.strip()
        try:
            json.loads(c)
            return c
        except json.JSONDecodeError:
            pass
    s, e = resp.text.find("{"), resp.text.rfind("}")
    if s != -1 and e > s:
        return resp.text[s:e + 1]
    raise ValueError("응답에서 JSON 을 찾지 못했습니다")
