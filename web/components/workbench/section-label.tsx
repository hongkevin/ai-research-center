/** 열 안의 구획 제목. 작고 조용하게 — 읽을 것은 내용이지 제목이 아니다. */
export function SectionLabel({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <h2
      className={`text-[11px] uppercase tracking-[0.09em] text-muted-foreground font-semibold mb-2.5 ${className}`}
    >
      {children}
    </h2>
  );
}

/** 좌우로 벌린 한 줄. 왼쪽이 이름, 오른쪽이 값. */
export function KeyValue({
  label,
  children,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex justify-between gap-2.5 text-[13px] py-0.5">
      <span>{label}</span>
      <span className="text-muted-foreground text-right">{children}</span>
    </div>
  );
}

/** 보조 설명. 화면의 판단 근거를 적는 자리라 자주 쓴다. */
export function Hint({ children }: { children: React.ReactNode }) {
  return <p className="text-[11.5px] text-muted-foreground mt-1.5 leading-relaxed">{children}</p>;
}
