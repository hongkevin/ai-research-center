# AI Research Center

코스닥 미커버 종목의 **실적 리뷰 노트를 자동 생성**하는 시스템. AI가 초안을 만들고 사람이 검토한 뒤 발간하는 세미오토 구조로, 모든 수치는 결정적 코드가 계산하고 산식·출처를 전면 공개하는 것을 원칙으로 한다.

## 설계 문서

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 확정 결정 로그, 규제 가드레일, S1~S6 파이프라인, 데이터 레이어, 로드맵
- [docs/design-brief-v1.md](docs/design-brief-v1.md) — 6개 영역 리서치 종합 브리프 (근거·출처)

## 셋업

```bash
python3 -m venv .venv && source .venv/bin/activate   # 또는: uv venv
pip install -e ".[dev]"                              # 또는: uv pip install -e ".[dev]"
pytest
```

## API 키

`.env.example`을 `.env`로 복사하고 키를 채운다:

| 키 | 발급처 | 용도 |
|---|---|---|
| `DART_API_KEY` | [OpenDART](https://opendart.fss.or.kr) | 재무제표·공시 (핵심 기둥) |
| `KRX_API_KEY` | [공공데이터포털](https://www.data.go.kr) — 금융위 주식시세정보 | EOD 시세 |
| `NAVER_CLIENT_ID/SECRET` | [네이버 개발자센터](https://developers.naver.com) | 뉴스 스니펫 |
| `ANTHROPIC_API_KEY` | [Claude Platform](https://platform.claude.com) | 보고서 생성·검증 (5~6주차부터) |

## 구조

```
src/arc/
├── data/       # DataProvider 인터페이스 + KR(dart, krx_price, naver_news) / US(edgar, v2) 어댑터
├── store/      # DuckDB+Parquet point-in-time 저장소
├── finmodel/   # 결정적 계산: 추정·멀티플·시나리오·감도표 (TODO 3~4주차)
├── pipeline/   # S1~S6 오케스트레이션 (TODO)
├── llm/        # Claude 클라이언트, Number Registry (TODO 5~6주차)
├── verify/     # 검증 게이트 G0/G1/G2 (TODO 5~6주차)
└── render/     # Jinja2 → HTML/PDF (TODO)
```
