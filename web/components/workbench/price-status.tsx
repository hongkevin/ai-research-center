"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { getPriceStatus, refreshPrices, type PriceStatus } from "@/lib/api";

/**
 * 받아 둔 시세 상태 (D87).
 *
 * 왜 이 칸이 있나
 * ---------------
 * 배포 점검에서 나온 것: **서버에 시세를 받아 올 경로가 아예 없었다.** 그래서
 * 배포된 앱은 시세가 0개인 채로 돌았고, 브리프의 등락도 섹터 줄도 피어 후보도
 * 발굴도 전부 비었다.
 *
 * 그때 각 화면은 저마다 「시세를 아직 못 받았습니다」라고 정직하게 말했지만,
 * **원인이 한 군데라는 것을 말해 주는 곳이 없었다.** 네 화면에서 각각 다른
 * 고장을 본 것처럼 읽힌다.
 *
 * 이제 서버가 뜰 때와 6시간마다 알아서 받는다. 이 칸은 **그게 됐는지 보이고,
 * 기다리기 싫을 때 지금 받게 하는** 자리다.
 *
 * **다 받았으면 조용하다.** 매일 「시세 2,987종목 정상」이 떠 있으면 눈이 그
 * 자리를 지나치게 되고, 정작 빈 날에도 안 보인다.
 */
export function PriceStatusBar() {
  const [s, setStatus] = useState<PriceStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // 받는 중이면 따라간다. **끝나면 멈춘다** — 계속 두드릴 이유가 없다.
  // 한 이펙트로 둔 것은 첫 조회와 추적이 같은 일이기 때문이다: 열 때 한 번
  // 읽고, 도는 중이면 계속 읽는다.
  const running = s?.running ?? false;
  useEffect(() => {
    let alive = true;
    const read = async () => {
      try {
        const got = await getPriceStatus();
        if (alive) setStatus(got);
      } catch {
        /* 상태를 못 읽는 것으로 화면을 막지 않는다 */
      }
    };
    void read();
    if (!running) return () => {
      alive = false;
    };
    const timer = setInterval(() => void read(), 3000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [running]);

  async function run() {
    setBusy(true);
    setError("");
    try {
      setStatus(await refreshPrices());
    } catch (e) {
      setError(e instanceof Error ? e.message : "받지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  if (!s) return null;

  const empty = s.loaded === 0;
  const partial = !empty && s.market_on_disk === 0;
  // 다 받았고 도는 중도 아니면 **아무것도 안 그린다**
  if (!empty && !partial && !s.running && !s.error) return null;

  return (
    <div className="rounded-md border border-warn/50 bg-warn/5 px-3.5 py-2.5 text-[12px] leading-[1.8]">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <span className="font-medium text-warn">
          {s.running
            ? "시세를 받는 중입니다"
            : empty
              ? "시세를 아직 받지 않았습니다"
              : partial
                ? "시가총액·거래대금을 아직 받지 않았습니다"
                : "마지막 갱신이 실패했습니다"}
        </span>
        <span className="text-muted-foreground">
          {empty ? (
            <>
              등락 · 섹터 줄 · 피어 후보 · 발굴이 <strong>모두 여기서</strong>{" "}
              비어 있습니다.
            </>
          ) : partial ? (
            <>
              시총·거래대금이 없어 <strong>발굴과 STOCK DATA</strong>가 비어
              있습니다.
            </>
          ) : (
            <>
              종목 {s.symbols_on_disk.toLocaleString()}개 · 시장 데이터{" "}
              {s.market_on_disk.toLocaleString()}개
            </>
          )}
        </span>
        <Button
          size="sm"
          variant="outline"
          className="ml-auto h-6 text-[11px]"
          disabled={busy || s.running}
          onClick={() => void run()}
        >
          {s.running ? "받는 중…" : busy ? "시작하는 중…" : "지금 받기"}
        </Button>
      </div>

      {/* **얼마나 걸리는지 말한다.** 안 말하면 30초 만에 고장으로 읽는다 */}
      {s.running && (
        <p className="mt-1 text-muted-foreground">
          처음이면 몇 분 걸립니다 (거래일 하루당 한 번 부릅니다). 이 화면을
          떠나도 계속 받습니다.
        </p>
      )}
      {(s.error || error) && (
        <p className="mt-1 font-mono text-[11px] text-bad">{error || s.error}</p>
      )}
      {!s.running && s.history.length > 0 && (
        <p className="mt-1 font-mono text-[10.5px] text-muted-foreground">
          {s.history[s.history.length - 1]}
        </p>
      )}
    </div>
  );
}
