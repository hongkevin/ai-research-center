# AI Research Center

코스닥 미커버 종목의 **실적 리뷰 노트를 자동 생성**하는 시스템. AI가 초안을 만들고 사람이 검토한 뒤 발간하는 세미오토 구조로, 모든 수치는 결정적 코드가 계산하고 산식·출처를 전면 공개하는 것을 원칙으로 한다.

## 설계 문서

**[docs/README.md](docs/README.md) — 문서 지도부터 보십시오.**

- [docs/decisions.md](docs/decisions.md) — **현재 유효한 결정의 단일 원천** (D1~D13)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 시스템 설계: 공통 엔진 + 표면 2개, S1~S6 파이프라인, Number Registry, G0 게이트
- [docs/design-brief-v1.md](docs/design-brief-v1.md) — 6개 영역 리서치 종합 브리프 (근거·출처)
- [docs/research/](docs/research/) — 벤치마크 15건 역설계, 채점 루브릭 분석, 밸류에이션 경계, 인터뷰 설계

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
| `OPENAI_API_KEY` | [OpenAI Platform](https://platform.openai.com) | 서술 생성. 없으면 결정론 문장으로만 생성된다 |

서술 provider는 `llm/client.py`의 `PROVIDERS`에서 **키가 있는 것 중 첫 번째**를
씁니다. 지금 등록된 것은 `openai` · `deepseek` · `moonshot` · `zhipu`이고, 넷 다
OpenAI 호환 `/chat/completions`라 base_url과 모델명만 다릅니다.
`.env.example`의 `ANTHROPIC_API_KEY`는 **아직 배선되지 않았습니다** — Claude는
Messages API라 어댑터가 따로 필요합니다.

## 구조

```
src/arc/
├── data/       # DataProvider 인터페이스 + KR(dart, krx_price, naver_news) / US(edgar, v2) 어댑터
├── store/      # DuckDB+Parquet point-in-time 저장소
├── finmodel/   # 결정적 계산: 지표·부문·추정·밸류에이션·백테스트·렌즈
├── pipeline/   # S1~S6 오케스트레이션
├── llm/        # LLM 클라이언트(provider 추상화), Number Registry, 조사 교정
├── verify/     # 발간 게이트 G0
├── render/     # 수치에 출처를 달아 HTML로 · 차트
└── web/        # API · 인증 · 작업 큐(SSE) · 정적 파일 서빙

templates/      # 리포트 템플릿 (wheel 밖 — ARC_TEMPLATE_DIR로 지정)
web/            # 화면: Next.js + Tailwind + shadcn/ui (정적 익스포트)
```

## 이어서 작업하려면

[docs/HANDOFF.md](docs/HANDOFF.md) — 현재 상태·불변식·남은 과제.
결정의 이유는 [docs/decisions.md](docs/decisions.md)(D1~D37)에 있습니다.
화면을 만질 거라면 [web/README.md](web/README.md)를 먼저 보십시오.

## 배포

동료가 브라우저로 접속해 테스트하는 절차는 [docs/DEPLOY.md](docs/DEPLOY.md)를 보십시오.

요약: **Railway**(영속 볼륨 필요) · GitHub `main` 푸시 → 자동 배포 ·
접근 제어는 공유 비밀번호(`ARC_PASSWORD`).

Vercel은 이 앱에 맞지 않습니다 — 서버리스라 추정 이력(`.arc-store`)이
재시작마다 사라져 revision 추적이 죽습니다.
