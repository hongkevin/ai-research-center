"""render — S6 렌더링: Jinja2 → HTML/PDF (ARCHITECTURE.md §4.1).

역할:
  - templates/earnings_review.md.j2에 섹션 텍스트 + Number Registry 값을 주입해
    최종 Markdown 생성 (숫자 플레이스홀더 치환은 렌더링 시점에 수행).
  - Markdown → HTML → PDF (WeasyPrint) 변환.
  - provenance 링크(공시 원문 URL 등)를 인간 검토 화면에서 클릭 가능하게 유지.

TODO(5~6주차): markdown.py(치환·조립), pdf.py(WeasyPrint)
"""
