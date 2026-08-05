"use client";

import { useEffect, useRef, useState } from "react";

import { Input } from "@/components/ui/input";
import { Popover, PopoverContent } from "@/components/ui/popover";
import { searchCompanies, type CompanyHit } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 회사명·종목코드 검색.
 *
 * 종목코드를 외우지 않아도 되게 한다. corpCode.xml(전 상장사)을 서버가 캐시하므로
 * 외부 검색 API가 필요 없다.
 *
 * shadcn의 Command 대신 직접 키보드를 다루는 이유는 **포커스** 때문이다.
 * Command를 Popover 안에 넣으면 자기 입력칸으로 포커스를 가져가서, 타이핑하던
 * 자리에서 목록이 뜨는 이 화면의 흐름이 끊긴다. Popover는 위치 계산과 충돌
 * 회피에만 쓴다.
 */
/**
 * 입력값에서 서버에 보낼 종목코드를 꺼낸다.
 *
 * 화면에는 「카카오페이 (377300)」처럼 남기고 서버에는 `377300`을 보낸다.
 * 괄호 안 코드 → 여섯 자리만 친 경우 → 그 외에는 입력 그대로(이름 검색).
 */
export function symbolOf(input: string): string {
  const paren = input.match(/\((\d{6})\)/);
  if (paren) return paren[1];
  const bare = input.trim().match(/^\d{6}$/);
  return bare ? bare[0] : input.trim();
}

export function CompanySearch({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  const [hits, setHits] = useState<CompanyHit[]>([]);
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(false);
  const lastQuery = useRef("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const q = value.trim();
    if (q === lastQuery.current) return;
    lastQuery.current = q;
    // 빈 입력 처리까지 타이머 안에서 한다. effect 본문에서 바로 setState하면
    // 연쇄 렌더가 생기고 react-hooks/set-state-in-effect가 막는다.
    // 타이핑마다 때리면 서버가 corpCode 인덱스를 반복 훑기도 한다.
    const t = setTimeout(async () => {
      if (!q) {
        setHits([]);
        setOpen(false);
        return;
      }
      const results = await searchCompanies(q, 10);
      setHits(results);
      setActive(-1);
      setOpen(results.length > 0);
    }, 160);
    return () => clearTimeout(t);
  }, [value]);

  function pick(i: number) {
    const hit = hits[i];
    if (!hit) return;
    // **이름과 코드를 함께 남긴다.** 코드만 넣으면 뭘 골랐는지 화면에서
    // 사라지고, 이름만 넣으면 동명이인에서 서버가 되묻는다
    // (`_resolve_symbol`이 여럿이면 고르지 않고 알린다). 제출할 때
    // `symbolOf()`가 괄호 안 코드를 꺼내 쓴다.
    const label = `${hit.name} (${hit.symbol})`;
    onChange(label);
    lastQuery.current = label;
    setOpen(false);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!open) return;
    if (e.key === "ArrowDown") {
      setActive((a) => Math.min(a + 1, hits.length - 1));
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      setActive((a) => Math.max(a - 1, 0));
      e.preventDefault();
    } else if (e.key === "Enter" && active >= 0) {
      pick(active);
      e.preventDefault();
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Input
        ref={inputRef}
        id="symbol"
        name="symbol"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="파마리서치"
        autoComplete="off"
        disabled={disabled}
        required
      />

      <PopoverContent
        anchor={inputRef}
        align="start"
        className="w-[var(--anchor-width)] p-1"
        // 포커스를 가져가면 계속 타이핑할 수 없다
        initialFocus={false}
      >
        <ul className="max-h-[260px] overflow-y-auto">
          {hits.map((hit, i) => (
            <li key={hit.symbol}>
              <button
                type="button"
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(i)}
                className={cn(
                  "flex w-full items-center justify-between gap-2 rounded-sm px-2 py-1.5 text-left text-[13.5px]",
                  i === active && "bg-accent text-accent-foreground",
                )}
              >
                <span>{hit.name}</span>
                <span className="font-mono text-[12px] opacity-70">{hit.symbol}</span>
              </button>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
