"use client";

import { useEffect, useState } from "react";

import { getSectorSeeds, type SectorSeed } from "@/lib/api";

/**
 * 섹터 고르기 — **정답이 아니라 출발점.**
 *
 * 빈 화면에서 「방산·우주」를 직접 치고 종목을 하나씩 넣는 것은 마찰이 크다.
 * 그렇다고 분류를 우리가 확정해 주면 D68이 거부한 문제로 돌아간다 — KSIC는
 * 방산 4종목을 어느 자릿수에서도 한 그룹으로 못 묶는다. 그래서 시드다.
 *
 * **응집도를 함께 낸다.** 애널리스트 커버리지에서 뽑았고 시장 요인을 뺀 내부
 * 상관이 무작위(0.102)의 3~4.7배라는 것이 이 목록의 유일한 근거다 — 숨기면
 * 「우리가 정한 분류」로 읽힌다.
 *
 * **고르는 것으로 저장하지 않는다.** 아래 편집과 같은 저장 버튼에 실린다 —
 * 여기서만 서버에 바로 쓰면 저장 안 한 편집과 어긋난다.
 */
export function SectorPicker({
  taken,
  onAdopt,
}: {
  /** 이미 넣은 섹터 이름. 화면이 자기 상태로 안다 */
  taken: string[];
  onAdopt: (seed: SectorSeed) => void;
}) {
  const [seeds, setSeeds] = useState<SectorSeed[]>([]);
  const [baseline, setBaseline] = useState(0.102);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const got = await getSectorSeeds();
        if (!alive) return;
        setSeeds(got.seeds);
        setBaseline(got.random_baseline);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (error) return <p className="text-[12px] text-bad">{error}</p>;

  const has = new Set(taken);
  const open = seeds.filter((s) => !has.has(s.name));
  if (open.length === 0) return null;

  return (
    <section>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-[12px] font-semibold">
          섹터 고르기{" "}
          <span className="font-mono font-normal text-muted-foreground">
            {open.length}
          </span>
        </h3>
        <span className="text-[11.5px] text-muted-foreground">
          고르면 대표 종목이 <strong>「관심」으로</strong> 들어옵니다 — 커버할
          것만 옮기십시오
        </span>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        {open.map((s) => (
          <button
            key={s.name}
            type="button"
            onClick={() => onAdopt(s)}
            className="rounded-lg border px-3 py-2 text-left transition-colors hover:bg-accent/40"
          >
            <div className="flex items-baseline gap-2">
              <span className="text-[13px] font-medium">{s.name}</span>
              <span className="font-mono text-[10.5px] text-muted-foreground">
                {s.symbols.length}종목
              </span>
              {/* **응집도가 이 목록의 유일한 근거다.** 숨기면 「우리가 정한
                  분류」로 읽힌다. */}
              <span
                className="ml-auto font-mono text-[10.5px] text-num"
                title={`시장 요인을 뺀 내부 상관 ${s.cohesion.toFixed(2)} — 무작위 8종목은 ${baseline.toFixed(2)}`}
              >
                ×{(s.cohesion / baseline).toFixed(1)}
              </span>
            </div>
            <p className="mt-0.5 truncate text-[11.5px] text-muted-foreground">
              {s.companies.slice(0, 5).join(" · ")}
            </p>
          </button>
        ))}
      </div>

      <p className="mt-2 text-[11.5px] leading-[1.7] text-muted-foreground">
        애널리스트 커버리지에서 뽑았습니다 — 사람이 직업으로 만든 분류라 표준
        분류와 달리 실제로 묶입니다. <strong>×배수</strong>는 무작위 8종목 대비
        내부 상관이고, 이게 이 목록의 유일한 근거입니다. 없는 섹터는 위에서
        직접 만드십시오.
      </p>
    </section>
  );
}
