# 배포 — Railway

동료가 브라우저로 접속해 테스트할 수 있게 올리는 절차입니다.

---

## 왜 Railway인가 (Vercel이 아니라)

| | 이 앱이 필요한 것 | Vercel | Railway |
|---|---|---|---|
| **영속 디스크** | `.arc-store`가 추정 이력이다. 없으면 revision 추적이 죽는다 | ✗ 임시 저장소만 | ✓ 볼륨 |
| **웜 캐시** | `corpCode.xml`(상장사 3,981개, 1.5MB)을 프로세스 수명 동안 캐시 | ✗ 콜드스타트마다 재다운로드 | ✓ 상주 프로세스 |
| **긴 요청** | LLM 생성 ~35초 | △ Fluid 300초로 가능 | ✓ 제한 없음 |

결정적인 것은 **영속 디스크**입니다. Vercel 서버리스에 올리면 배포·콜드스타트마다
추정 이력이 사라지고, "직전 발간 대비 하향 −16.5%"가 영영 나오지 않습니다.
[D25·D27](decisions.md)이 통째로 무의미해집니다.

응답 시간은 이제 블로커가 아닙니다. 문제는 **시간이 아니라 상태**입니다.

> Vercel이 맞는 때가 옵니다 — 표면을 Next.js로 바꾸면 Vercel(프론트) +
> Railway(API)가 자연스럽습니다. `/api/reports`를 열어둔 게 그 자리입니다.

---

## 1회 설정

### 1. 저장소 푸시

```bash
git push origin main
```

> ⚠ 실제 키는 `.env`에만 둡니다. `.env.example`에는 값을 비워 두십시오 —
> 이 저장소에서 두 번 섞였던 자리입니다.
>
> 방어선이 둘 있습니다:
> - **pre-commit 훅** — 커밋을 막습니다. 한 번만 설치하십시오:
>   `git config core.hooksPath .githooks`
> - **CI** — 푸시 후 검사합니다. 늦으므로 훅이 1차 방어선입니다.

### 2. Railway 프로젝트

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. `ai-research-center` 선택. `railway.toml`과 `Dockerfile`을 자동으로 씁니다.

### 3. 볼륨

**Settings 안에 없습니다.** 프로젝트 캔버스에서 만듭니다:

- `⌘K`(맥) / `Ctrl+K` → "Volume" 검색, 또는
- 프로젝트 캔버스 **빈 곳을 우클릭** → Volume 추가

그다음 **연결할 서비스를 고르고**, 서비스 설정에서 **마운트 경로**를 지정합니다.

마운트 경로는 `/data`로 두면 Dockerfile 기본값(`ARC_STORE_DIR=/data/arc-store`)과
맞습니다. 다른 경로에 붙였다면 환경변수를 그에 맞춰 바꾸십시오 — 예를 들어
`/app/data`에 붙였으면 `ARC_STORE_DIR=/app/data/arc-store`.

무료 플랜은 프로젝트당 볼륨 1개·0.5GB입니다. 이 저장소는 Parquet 스냅샷이라
수십 KB 수준이므로 충분합니다.

**볼륨이 없어도 리포트는 생성됩니다.** 다만 컨테이너가 재시작할 때마다 추정
이력이 사라져 revision 추적이 항상 "직전 보고서 없음"이 됩니다. 붙었는지는
아래 `/api/health`의 `store.writable`로 확인하십시오.

### 4. 리전 — 한국에 가깝게

**Settings → Region**에서 `Asia Southeast`(싱가포르)로 바꾸십시오.

이 앱은 요청 한 건에 DART(한국)를 여러 번 호출하고, 그중 사업보고서 원문은
5~8MB입니다. 리전이 미국이면 그 왕복이 전부 태평양을 건넙니다 — 실측으로
같은 작업이 로컬 1.9초 vs 미국 리전 12.2초였습니다.

싱가포르가 Railway가 제공하는 가장 가까운 리전입니다. 한국 리전은 없습니다.

### 5. 환경변수

**Variables** 탭에서 설정합니다.

| 변수 | 값 | 비고 |
|---|---|---|
| `DART_API_KEY` | (발급값) | 필수. 없으면 아무것도 안 된다 |
| `OPENAI_API_KEY` | (발급값) | 없으면 결정론 문장으로만 생성 |
| `ARC_PASSWORD` | 동료와 공유할 비밀번호 | **필수.** 없으면 인증 없이 열린다 |
| `ARC_USERNAME` | `arc` | 기본값 |
| `ARC_LLM_LIMIT` | `200` | 프로세스당 LLM 호출 상한 |
| `ARC_STORE_DIR` | `/data/arc-store` | Dockerfile 기본값. 볼륨과 맞춘다 |
| `ARC_STATIC_DIR` | `/app/static` | Dockerfile 기본값. 화면(Next.js 정적 익스포트) 위치. 건드릴 일 없다 |

`ARC_DEV_ORIGIN`은 **설정하지 마십시오.** `next dev`를 따로 띄울 때만 쓰는
개발용 CORS 스위치이고, 켜진 채로 배포되면 다른 사이트가 이 API를 부를 수 있습니다.

`PORT`는 Railway가 주입하므로 설정하지 마십시오.

### 6. 확인

```bash
curl https://<앱주소>/api/health
```
```json
{
  "status": "ok",
  "dart_key": true,
  "llm_key": true,
  "auth": true,
  "llm_used": 0,
  "llm_limit": 200,
  "store": { "writable": true, "path": "/data/arc-store" }
}
```

확인할 것 두 가지:

- `auth`가 `false`면 **비밀번호가 안 걸린 상태**입니다. 즉시 `ARC_PASSWORD`를
  설정하십시오 — 주소를 아는 누구나 서버의 LLM 키를 쓸 수 있습니다.
- `store.writable`이 `false`면 볼륨이 안 붙었거나 `ARC_STORE_DIR`과 마운트
  경로가 다릅니다. `reason`에 사유가 나옵니다. 리포트 생성은 되지만 revision
  추적이 죽습니다.

---

## 이후 배포

`main`에 푸시하면 Railway가 자동으로 다시 빌드·배포합니다.

GitHub Actions(`ci.yml`)가 같은 푸시에서 린트·테스트·Docker 빌드·키 검사를
돌립니다. **CI는 배포를 막지 않습니다** — Railway는 GitHub 이벤트를 직접
받습니다. CI가 빨간 채로 배포되는 게 싫으면 Railway의 "Wait for CI" 옵션을
켜십시오.

---

## 접근 제어

공유 비밀번호(HTTP Basic)입니다. 브라우저 기본 로그인 창이 뜹니다.

- **누가 눌렀는지는 알 수 없습니다.** 비밀번호 하나를 돌려 쓰기 때문입니다.
  사용량을 사람별로 나눠 봐야 하면 초대 코드나 SSO로 올려야 합니다.
- `/api/health`만 인증 없이 열려 있습니다. 플랫폼 헬스체크가 인증을 통과할
  수 없기 때문입니다. 이 엔드포인트는 키 값을 노출하지 않고 존재 여부만
  불리언으로 알려줍니다.

---

## 비용

| 항목 | 대략 |
|---|---|
| Railway Hobby | $5/월 + 사용량 |
| LLM (건당) | ~$0.005 — 프롬프트가 커져 초기 $0.0019보다 올랐다 |
| DART | 무료 (일 20,000건) |

`ARC_LLM_LIMIT`이 프로세스당 상한입니다. 도달하면 **LLM만 끄고** 결정론
생성은 계속됩니다 — 화면이 죽는 것보다 낫습니다. 상한은 재시작하면
초기화되므로 정확한 회계가 아니라 폭주 방지 장치입니다.

---

## 알려진 제약

- **워커 1개.** corpCode 캐시와 LLM 예산이 프로세스 메모리에 있어 워커를 늘리면
  캐시가 중복되고 예산이 워커 수만큼 곱해집니다. 동시 접속이 늘면 그때 둘 다
  외부 저장소로 옮기고 늘립니다.
- **첫 요청이 느립니다.** corpCode.xml을 받아 파싱합니다(~1.2초). 이후는 캐시.
- **LLM 생성이 30~40초.** 사업 서술·산업 배경까지 두 번 호출합니다. 화면에는
  단계별 진행이 실시간으로 표시됩니다(SSE). 실측: 원문 수집 2초 → LLM 서술
  17초 → 산업 배경 4초 → 게이트 즉시.
- **작업 상태가 메모리에 있습니다.** 워커 1개 전제입니다. 배포가 재시작하면
  진행 중이던 작업은 사라집니다(결과 페이지가 홈으로 되돌립니다).
- **리포트 템플릿은 wheel에 없습니다.** `src/arc` 밖에 있어서입니다. Dockerfile이
  `ARC_TEMPLATE_DIR`로 경로를 지정합니다 — 다른 방식으로 배포하면 이걸 맞춰야
  합니다.
