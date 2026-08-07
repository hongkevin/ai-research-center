"use client";

import type { LensView } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 관점 — **엔진이 만들어 놓고 화면에 못 오던 것** (D35 · D73).
 *
 * 코스닥 다섯 곳이 글자까지 같은 문장을 받은 사고에서 나온 장치다. 렌즈는
 * **부호 하나로 접지 않는다** — 「주된 발견 + 단서 + 다음에 볼 것」으로 낸다.
 *
 * 세 가지를 지킨다:
 *
 * * **앞선 질문에 답하지 못했으면 결론을 내지 않는다.** `headline`이 비는
 *   경우가 그것이고, 그때는 무엇을 못 봤는지를 대신 적는다
 * * **침묵한 렌즈도 낸다.** 통째로 빠지면 「이 회사엔 볼 관점이 없구나」로
 *   읽힌다. 못 본 것을 적는 편이 정직하다
 * * **단서는 주된 발견과 방향이 다르다.** 붙여 놓아야 "자산은 버는 곳에 있다
 *   — 다만 그 수익률이 부채에서 온다"가 한 덩어리로 읽힌다
 *
 * 본문(`body_html`)과 같은 글이고 숫자는 같은 레지스트리에서 온다 — 클릭하면
 * 출처가 뜨는 `<span>`이 그대로 들어 있다.
 */
export function LensPanel({
  lenses,
  tensions,
}: {
  lenses: LensView[];
  tensions: string[];
}) {
  if (lenses.length === 0) return null;

  return (
    <section className="mt-10 border-t pt-6">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-2.5">
        <h2 className="text-[15px] font-semibold">관점</h2>
        <span className="text-[11.5px] text-muted-foreground">
          같은 숫자를 다른 질문으로 읽은 것입니다 — 답하지 못한 질문은 그대로
          적습니다
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {lenses.map((v) => (
          <Lens key={v.label} view={v} />
        ))}
      </div>

      {/* **관전 포인트는 렌즈가 갈리는 지점이다** (D35). 한 렌즈가 좋다고
          하고 다른 렌즈가 아니라고 할 때, 그 어긋남이 볼 거리다. */}
      {tensions.length > 0 && (
        <div className="mt-4 rounded-lg border border-warn/50 px-3.5 py-3">
          <p className="text-[12px] font-semibold">관점이 갈리는 지점</p>
          <ul className="mt-1.5 space-y-1">
            {tensions.map((t, i) => (
              <li key={i} className="text-[12.5px] leading-[1.75]">
                {t}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function Lens({ view: v }: { view: LensView }) {
  const silent = !v.headline;
  return (
    <div
      className={cn(
        "rounded-lg border px-3.5 py-3",
        silent && "border-dashed bg-muted/20",
      )}
    >
      <div className="flex items-baseline gap-2">
        <span className="text-[13px] font-medium">{v.label}</span>
        {silent && (
          <span className="font-mono text-[10px] text-muted-foreground">
            결론 없음
          </span>
        )}
      </div>
      <p className="mt-0.5 text-[11px] text-muted-foreground">{v.question}</p>

      {/* **주된 발견.** 없으면 왜 없는지가 그 자리에 온다 */}
      {v.headline ? (
        <p
          className="mt-2 text-[13px] leading-[1.8]"
          dangerouslySetInnerHTML={{ __html: v.headline }}
        />
      ) : (
        <p className="mt-2 text-[12.5px] leading-[1.75] text-muted-foreground">
          {v.note || "이 관점으로 볼 근거를 공시에서 찾지 못했습니다."}
        </p>
      )}

      {/* **단서는 주된 발견에 붙어야 한다.** 떼어 놓으면 결론만 읽힌다 */}
      {v.caveats.length > 0 && (
        <ul className="mt-1.5 space-y-1">
          {v.caveats.map((t, i) => (
            <li
              key={i}
              className="border-l-2 border-warn/60 pl-2 text-[12.5px] leading-[1.75] text-muted-foreground"
              dangerouslySetInnerHTML={{ __html: `다만 ${t}` }}
            />
          ))}
        </ul>
      )}

      {v.readings.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {v.readings.map((t, i) => (
            <li
              key={i}
              className="text-[12px] leading-[1.7] text-muted-foreground"
              dangerouslySetInnerHTML={{ __html: t }}
            />
          ))}
        </ul>
      )}

      {v.watch && (
        <p className="mt-2 border-t pt-2 text-[12px] leading-[1.7]">
          <span className="mr-1.5 text-[10px] text-muted-foreground">
            다음에 볼 것
          </span>
          {v.watch}
        </p>
      )}

      {/* **답하지 못한 질문을 적는다.** 이게 이 화면에서 가장 정직한 부분이다 */}
      {v.unanswered.length > 0 && (
        <p className="mt-1.5 text-[11px] leading-[1.65] text-muted-foreground">
          못 본 것: {v.unanswered.join(" · ")}
        </p>
      )}
    </div>
  );
}
