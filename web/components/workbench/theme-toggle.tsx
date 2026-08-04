"use client";

import { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * 라이트/다크 전환.
 *
 * 실제 상태는 React가 아니라 **DOM**에 있다 — `layout.tsx`의 인라인 스크립트가
 * 페인트 전에 `<html>`에 `.dark`를 붙이기 때문이다(그래야 밝은 화면이 한 번
 * 번쩍이지 않는다). 그래서 `useState`로 복제하지 않고 `useSyncExternalStore`로
 * DOM을 직접 읽는다. 복제하면 스크립트가 정한 값과 갈라진다.
 *
 * 선택은 `localStorage.theme`에 남는다. 없으면 `prefers-color-scheme`을 따른다.
 */

function subscribe(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["class"],
  });
  return () => observer.disconnect();
}

const isDark = () => document.documentElement.classList.contains("dark");

export function ThemeToggle() {
  // 정적 익스포트라 빌드 시점에는 DOM이 없다 — 그때는 라이트로 그리고,
  // 하이드레이션 직후 실제 값으로 맞춰진다.
  const dark = useSyncExternalStore(subscribe, isDark, () => false);

  function toggle() {
    const next = !isDark();
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      // 사파리 프라이빗 모드 등에서 막힌다. 이번 세션에만 적용되고 끝난다.
    }
  }

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label={dark ? "라이트 모드로 전환" : "다크 모드로 전환"}
      title={dark ? "라이트 모드로 전환" : "다크 모드로 전환"}
    >
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </Button>
  );
}
