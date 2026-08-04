# 화면 (발간 표면)

실적 리뷰 노트 작업대. Next.js 16 App Router + Tailwind v4 + shadcn/ui.

**정적으로 익스포트해 FastAPI가 서빙한다.** Node는 빌드에만 쓰고 런타임에는
없다 — `.arc-store`(추정 이력)가 볼륨에 있어야 하고 corpCode 캐시가 프로세스
메모리에 있어서 컨테이너를 하나로 유지해야 하기 때문이다.

## 개발

```bash
# 1) 빌드해서 실제 배포와 같은 형태로 보기 (설정 없음)
npm run build && (cd .. && arc web)      # http://localhost:8000

# 2) 핫 리로드가 필요할 때 — 두 서버를 따로 띄운다
npm run dev                               # 3000
ARC_DEV_ORIGIN=http://localhost:3000 \
  NEXT_PUBLIC_API_BASE=http://localhost:8000 arc web   # 8000
```

`ARC_DEV_ORIGIN`은 **개발 전용**이다. 비워두면 CORS가 꺼진 채로 뜬다 —
배포에서는 같은 출처라 필요 없다.

## 구조

```
app/page.tsx              작업대 한 화면 (왼쪽 조작 · 가운데 노트 · 오른쪽 근거)
app/note.css              서버가 발행하는 고정 클래스 (.num, .note …)
components/note/          노트 본문 주입 + 수치 출처 팝오버
components/workbench/     폼 · 회사 검색 · 근거 패널
lib/api.ts                /api/* 클라이언트 (타입은 app.py의 ViewModel)
lib/use-generation.ts     생성 → SSE 진행 → 결과
```

## 건드리기 전에 알아야 할 것

**노트 본문의 숫자는 React가 만들지 않는다.** `src/arc/render/html.py`가
수치마다 `<span class="num" data-key … data-url>`을 붙여 내보내고, 화면은
그것을 그대로 주입한다. React에서 숫자를 다시 포맷하면 레지스트리를 거치는
경로가 둘이 되어 제품의 불변식이 깨진다 (`docs/HANDOFF.md` 「불변식 1」).

그래서 `app/note.css`의 선택자는 Tailwind 유틸리티로 바꿀 수 없다. 서버가
내보내는 이름과 정확히 맞아야 한다.

**게이트가 막은 초안은 렌더하지 않는다.** `gate_passed`가 false면 본문 자리에
위반 내역만 낸다. 차단된 초안을 보여주면 검토자가 결과로 착각한다.

## `AGENTS.md`

create-next-app이 넣은 파일이다. Next.js 16이 이전 버전과 API가 다르니
`node_modules/next/dist/docs/`를 보라는 안내다 — 지우지 말 것. 실제로 이
이관에서 shadcn이 Radix가 아니라 Base UI를 쓴다는 걸 그 경로에서 확인했다.
