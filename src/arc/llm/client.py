"""LLM 클라이언트 추상화 + provider 레지스트리.

ARCHITECTURE.md §4.4는 Claude 기준으로 작성됐다. 실제로는 여러 provider를
비교해 쓰므로 인터페이스를 분리한다.

이 추상화가 성립하는 이유는 Number Registry에 있다. 숫자는 결정적 코드가
만들고 LLM은 플레이스홀더만 쓰므로, **어느 모델이 문장을 썼는지는 G0 게이트
입장에서 무관하다.** 모델 교체가 수치 신뢰성에 영향을 주지 않는다.

주요 provider가 모두 OpenAI 호환 `/chat/completions`를 제공하므로 base_url과
모델명만 바꾸면 된다. 새 provider 추가는 `PROVIDERS`에 한 줄이다.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum


class Tier(StrEnum):
    """역할별 모델 티어 (ARCHITECTURE.md §4.4)."""

    LIGHT = "light"  # S1 정규화·경량 처리
    WRITE = "write"  # S2·S4 분석·작성
    VERIFY = "verify"  # S5 검증·감수


@dataclass(frozen=True)
class Pricing:
    """1M 토큰당 USD. 비용 실측·비교용."""

    input: float
    output: float

    def cost(self, in_tok: int | None, out_tok: int | None) -> float | None:
        if in_tok is None or out_tok is None:
            return None
        return in_tok * self.input / 1e6 + out_tok * self.output / 1e6


@dataclass(frozen=True)
class ProviderSpec:
    """OpenAI 호환 provider 1개의 접속 정보."""

    name: str
    base_url: str
    env_key: str
    models: dict[Tier, str]
    pricing: dict[Tier, Pricing]
    # GPT-5.x 계열은 max_tokens를 거부하고 max_completion_tokens를 쓴다.
    # 다른 OpenAI 호환 provider는 대부분 max_tokens 그대로다.
    token_param: str = "max_tokens"
    note: str = ""


# ── provider 레지스트리 ──────────────────────────────────────────────
# 단가는 2026-08-01 기준 공개 정보. 변동이 잦으니 비교 전에 확인할 것.
PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        name="openai",
        base_url="https://api.openai.com/v1",
        env_key="OPENAI_API_KEY",
        models={
            Tier.LIGHT: "gpt-5.6-luna",
            Tier.WRITE: "gpt-5.6-luna",
            Tier.VERIFY: "gpt-5.6-sol",
        },
        pricing={
            Tier.LIGHT: Pricing(0.20, 1.20),  # Luna (2026-07-30 80% 인하)
            Tier.WRITE: Pricing(0.20, 1.20),  # Luna — 제약된 작문이라 저가로 시작해 벤치마크로 검증
            Tier.VERIFY: Pricing(5.00, 30.00),  # Sol
        },
        token_param="max_completion_tokens",
    ),
    "deepseek": ProviderSpec(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        env_key="DEEPSEEK_API_KEY",
        models={t: "deepseek-chat" for t in Tier},
        pricing={t: Pricing(0.435, 0.87) for t in Tier},
        note="V4-Pro 영구 인하가 적용된 단가. 캐시 히트 시 입력이 크게 떨어진다.",
    ),
    "moonshot": ProviderSpec(
        name="moonshot",
        base_url="https://api.moonshot.ai/v1",
        env_key="MOONSHOT_API_KEY",
        models={t: "kimi-k2-turbo-preview" for t in Tier},
        pricing={t: Pricing(0.60, 2.50) for t in Tier},
        note="모델명·단가는 계정 등급에 따라 다르다. 비교 전 콘솔에서 확인할 것.",
    ),
    "zhipu": ProviderSpec(
        name="zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        env_key="ZHIPU_API_KEY",
        models={t: "glm-4-plus" for t in Tier},
        pricing={t: Pricing(0.60, 3.20) for t in Tier},
        note="GLM 계열. 모델명은 콘솔에서 확인할 것.",
    ),
}


@dataclass(frozen=True)
class Completion:
    """LLM 응답 + 사용량 (비용 실측용)."""

    text: str
    model: str
    provider: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_s: float | None = None
    cost_usd: float | None = None


class LLMClient(ABC):
    name: str

    @abstractmethod
    def complete(
        self, *, system: str, user: str, tier: Tier = Tier.WRITE, max_tokens: int = 4096
    ) -> Completion: ...

    @abstractmethod
    def healthcheck(self) -> tuple[bool, str]: ...


class OpenAICompatClient(LLMClient):
    """OpenAI 호환 `/chat/completions`를 쓰는 provider 공통 클라이언트."""

    def __init__(
        self,
        spec: ProviderSpec,
        api_key: str | None = None,
        model_override: str | None = None,
        timeout: float = 180.0,
    ) -> None:
        self.spec = spec
        self.name = spec.name
        self.api_key = api_key or os.environ.get(spec.env_key, "")
        if not self.api_key:
            raise ValueError(f"{spec.env_key}가 설정되지 않았습니다 (.env 참조)")
        self.model_override = model_override
        self.timeout = timeout

    def model_for(self, tier: Tier) -> str:
        return self.model_override or self.spec.models[tier]

    def complete(
        self, *, system: str, user: str, tier: Tier = Tier.WRITE, max_tokens: int = 4096
    ) -> Completion:
        import httpx

        model = self.model_for(tier)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            self.spec.token_param: max_tokens,
        }
        t0 = time.monotonic()
        r = httpx.post(
            f"{self.spec.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        latency = time.monotonic() - t0

        usage = data.get("usage") or {}
        in_tok = usage.get("prompt_tokens")
        out_tok = usage.get("completion_tokens")
        price = self.spec.pricing.get(tier)

        return Completion(
            text=data["choices"][0]["message"]["content"] or "",
            model=data.get("model", model),
            provider=self.name,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_s=latency,
            cost_usd=price.cost(in_tok, out_tok) if price else None,
        )

    def healthcheck(self) -> tuple[bool, str]:
        """가장 싼 티어로 최소 요청 1건.

        추론 모델(GPT-5.x)은 max_completion_tokens를 추론에 먼저 소모하므로
        예산을 너무 작게 주면 본문이 빈 문자열로 돌아온다. 여유를 둔다.
        """
        import httpx

        try:
            c = self.complete(
                system="Reply with the single word: ok",
                user="ping",
                tier=Tier.LIGHT,
                max_tokens=512,
            )
        except httpx.HTTPStatusError as e:
            try:
                err = e.response.json().get("error", {})
                detail = (
                    f"{err.get('code') or err.get('type')} — {str(err.get('message', ''))[:70]}"
                )
            except Exception:  # noqa: BLE001
                detail = e.response.text[:80]
            return False, f"HTTP {e.response.status_code} · {detail}"
        except Exception as e:  # noqa: BLE001 — 진단 목적
            return False, f"{type(e).__name__}: {e}"
        return True, f"{c.model} OK ({c.latency_s:.1f}s, {c.text.strip()[:16]!r})"


def available_providers() -> list[str]:
    """환경에 키가 있는 provider 목록."""
    return [n for n, s in PROVIDERS.items() if os.environ.get(s.env_key)]


def get_client(provider: str | None = None, model: str | None = None) -> LLMClient:
    """키가 있는 provider로 클라이언트를 만든다."""
    if provider is None:
        avail = available_providers()
        if not avail:
            keys = ", ".join(s.env_key for s in PROVIDERS.values())
            raise ValueError(f"사용 가능한 LLM 키가 없습니다. .env에 다음 중 하나: {keys}")
        provider = avail[0]
    if provider not in PROVIDERS:
        raise ValueError(f"모르는 provider: {provider!r}. 가능: {', '.join(PROVIDERS)}")
    return OpenAICompatClient(PROVIDERS[provider], model_override=model)
