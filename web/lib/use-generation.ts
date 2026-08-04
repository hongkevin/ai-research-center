"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { eventsUrl, fetchResult, startJob, type JobRequest, type ViewModel } from "./api";

/**
 * 생성 한 건의 수명 — 시작 → 진행 스트림 → 결과.
 *
 * LLM까지 켜면 30~40초가 걸린다. 화면에 아무 반응이 없으면 사용자는 멈춘 줄
 * 알고 다시 누르고, 그러면 **LLM 호출과 요금이 두 배**가 된다 (`web/jobs.py`).
 *
 * 서버의 SSE는 진작 완성돼 있었지만 부르는 쪽이 없었다 — 이전 화면에는
 * 진행 표시 마크업과 CSS만 있고 `EventSource`가 없어서, 폼 POST가 끝날 때까지
 * 그냥 기다렸다. 이 훅이 그 자리를 채운다.
 */

export interface Step {
  key: string;
  message: string;
}

export type Phase = "idle" | "running" | "done" | "error";

export function useGeneration() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [steps, setSteps] = useState<Step[]>([]);
  const [vm, setVm] = useState<ViewModel | null>(null);
  const [error, setError] = useState("");
  const [elapsed, setElapsed] = useState(0);

  const source = useRef<EventSource | null>(null);
  const startedAt = useRef(0);

  const close = useCallback(() => {
    source.current?.close();
    source.current = null;
  }, []);

  useEffect(() => close, [close]);

  // 경과 시간 — 오래 걸리는 동안 "살아 있다"는 유일한 신호일 때가 있다
  useEffect(() => {
    if (phase !== "running") return;
    const t = setInterval(() => {
      setElapsed(Math.round((Date.now() - startedAt.current) / 1000));
    }, 1000);
    return () => clearInterval(t);
  }, [phase]);

  const run = useCallback(
    async (req: JobRequest) => {
      close();
      setPhase("running");
      setSteps([]);
      setVm(null);
      setError("");
      setElapsed(0);
      startedAt.current = Date.now();

      let jobId: string;
      try {
        jobId = await startJob(req);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setPhase("error");
        return;
      }

      const es = new EventSource(eventsUrl(jobId));
      source.current = es;

      es.addEventListener("step", (e) => {
        try {
          setSteps((prev) => [...prev, JSON.parse((e as MessageEvent).data)]);
        } catch {
          /* 한 건을 못 읽어도 생성은 계속된다 */
        }
      });

      es.addEventListener("done", async (e) => {
        close();
        let ok = true;
        let reason = "";
        try {
          const d = JSON.parse((e as MessageEvent).data);
          ok = d.ok;
          reason = d.error ?? "";
        } catch {
          /* 파싱에 실패해도 결과는 받아 본다 */
        }
        if (!ok) {
          setError(reason || "생성에 실패했습니다.");
          setPhase("error");
          return;
        }
        try {
          setVm(await fetchResult(jobId));
          setPhase("done");
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err));
          setPhase("error");
        }
      });

      // 스트림이 끊기면 매달려 있지 않게 한다. 정상 종료는 `done`에서 이미
      // 닫았으므로, 여기 오는 것은 프록시가 끊었거나 서버가 죽은 경우다.
      es.onerror = () => {
        if (source.current === null) return;
        close();
        setError("진행 스트림이 끊겼습니다. 다시 시도해 주세요.");
        setPhase("error");
      };
    },
    [close],
  );

  return { phase, steps, vm, error, elapsed, run };
}
