"""llm — Claude 클라이언트, 프롬프트, Number Registry (ARCHITECTURE.md §4.2, §4.4).

역할:
  - Claude API 클라이언트 래퍼 (Batch API 기본, prompt caching)
  - 단계별 프롬프트 템플릿 (S1 정규화=Haiku급, S2/S4=Sonnet급, S5=Opus급)
  - Number Registry: 보고서에 등장 가능한 모든 수치의 단일 원천

TODO(5~6주차): client.py(Batch/캐싱), prompts/ 디렉터리
"""

from arc.llm.number_registry import NumberEntry, NumberRegistry

__all__ = ["NumberEntry", "NumberRegistry"]
