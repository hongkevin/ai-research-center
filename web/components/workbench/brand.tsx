import { cn } from "@/lib/utils";

/**
 * 워드마크.
 *
 * 확장형의 **A·R·C만 밝게** 둔다 — 약어가 어디서 왔는지가 명도 차이로 보인다.
 * **색상은 쓰지 않는다.** 세 글자에 서로 다른 색을 얹으면 금융 도구가 아니라
 * 소비자 앱처럼 읽힌다. 대비만으로 충분하다.
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
      ARC
      {expansion && (
        <span className="ml-1.5 font-normal text-muted-foreground">
          - <span className="text-foreground">A</span>I{" "}
          <span className="text-foreground">R</span>esearch{" "}
          <span className="text-foreground">C</span>enter
        </span>
      )}
    </span>
  );
}

/** 담백한 설명. 약속이 아니라 하는 일이다. */
export const BRAND_LINE = "공시에서 뽑은 숫자로 리서치 초안을 만들고 검토합니다.";
