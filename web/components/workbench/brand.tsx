import { cn } from "@/lib/utils";

/**
 * 워드마크.
 *
 * 세 글자에 각각 제품 팔레트의 색을 준다 — 새 색을 들이지 않고 이미 뜻을 가진
 * 색을 쓴다: 수치(파랑) · 추정(보라) · 통과(초록). 굵기와 크기는 건드리지
 * 않는다. 장식이 아니라 표식이라 그 이상은 이 바닥에서 가볍게 읽힌다.
 */
export function Brand({
  className,
  expansion = true,
}: {
  className?: string;
  /** 「AI Research Center」를 함께 낼 것인가 */
  expansion?: boolean;
}) {
  return (
    <span className={cn("font-semibold tracking-tight", className)}>
      <span className="text-num">A</span>
      <span className="text-est">R</span>
      <span className="text-ok">C</span>
      {expansion && (
        <span className="ml-1.5 font-normal text-muted-foreground">AI Research Center</span>
      )}
    </span>
  );
}

/** 담백한 설명. 약속이 아니라 하는 일이다. */
export const BRAND_LINE = "공시에서 뽑은 숫자로 리서치 초안을 만들고 검토합니다.";
