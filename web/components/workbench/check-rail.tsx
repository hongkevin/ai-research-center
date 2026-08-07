"use client";

import { RailSection } from "@/components/workbench/rail-section";
import type { ViewModel } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * 검산 — **엔진이 이미 대조해 놓은 것** (D73).
 *
 * 각 산출물이 자기 진단 필드를 들고 있다: 부문 손익은 총계 열로 검산하고,
 * 밸류에이션은 재무제표 EPS와 배당공시 EPS를 교차 대조하고, 주요정보는 못
 * 받은 항목의 이름을 `unavailable`에 남긴다. **그 전부가 화면에 안 나왔다.**
 *
 * 세 가지 구분이 이 레일의 존재 이유다:
 *
 * * **「없다」와 「못 받았다」** — 배당을 안 준 것과 배당 정보를 못 받은 것은
 *   다른 얘기인데 지금까지 둘 다 빈칸이었다
 * * **「정상 부재」와 「결함」** — SK하이닉스에 부문 손익이 없는 것은 단일
 *   부문이라 정상이다(D33이 정확히 거부한 것). DART 조회 실패는 결함이다
 * * **「맞다」와 「안 맞다」** — 교차검증은 통과했을 때도 말해야 한다. 안
 *   보이면 검산을 한 적이 없는 것과 구별되지 않는다
 */
export function CheckRail({ vm }: { vm: ViewModel }) {
  const val = vm.valuation ?? {};
  const sp = vm.segment_profit ?? {};
  const info = vm.report_info ?? {};
  const missing = info.unavailable ?? [];

  const checks = countChecks(vm);
  if (checks === 0 && !vm.info_error) return null;

  return (
    <div className="mt-6 space-y-1 border-t pt-5">
      <RailSection title="검산" count={checks} defaultOpen={hasProblem(vm)}>
        <div className="space-y-2.5">
          {/* **조회 실패를 조용히 넘기지 않는다.** */}
          {vm.info_error && (
            <p className="rounded-md border border-bad/50 px-2.5 py-2 text-[11.5px] leading-[1.7] text-bad">
              주요정보를 못 읽었습니다 — {vm.info_error}
            </p>
          )}

          {val.eps_stmt != null && val.eps_disclosed != null && (
            <Check
              label="EPS 교차검증"
              ok={Math.abs(val.eps_gap_pct ?? 0) < 1}
              detail={`재무제표 ${val.eps_stmt.toLocaleString()}원 · 공시 ${val.eps_disclosed.toLocaleString()}원`}
              value={`${(val.eps_gap_pct ?? 0) >= 0 ? "+" : ""}${(val.eps_gap_pct ?? 0).toFixed(2)}%`}
              why="재무제표의 희석EPS와 배당공시의 주당순이익은 같아야 합니다. 어긋나면 둘 중 하나가 틀렸다는 뜻입니다."
            />
          )}

          {val.shares_issued != null && (
            <Check
              label="주식수"
              ok={!!val.shares_reconciled}
              detail={`발행 ${val.shares_issued.toLocaleString()}주${val.has_preferred ? " · 우선주 있음" : ""}`}
              why="주식수가 공시와 안 맞으면 BPS·EPS·배당수익률이 전부 흔들립니다."
            />
          )}

          {sp.reconciled != null && (sp.lines?.length ?? 0) > 0 && (
            <Check
              label="부문 합계 vs 손익계산서"
              ok={!!sp.reconciled}
              detail={`${sp.lines?.length}개 부문${sp.section_title ? ` · ${sp.section_title}` : ""}`}
              value={
                sp.revenue_gap_pct != null
                  ? `${sp.revenue_gap_pct >= 0 ? "+" : ""}${sp.revenue_gap_pct.toFixed(2)}%`
                  : undefined
              }
              why="주석의 총계 열과 손익계산서를 대조합니다. 원문에서 뽑은 숫자라 검산 없이는 쓸 수 없습니다."
            />
          )}

          {/* **정상 부재는 결함이 아니다.** 사유가 있으면 그대로 적는다 */}
          {sp.usable === false && sp.note && (
            <p className="rounded-md border border-dashed px-2.5 py-2 text-[11.5px] leading-[1.7] text-muted-foreground">
              <span className="mr-1.5 font-mono text-[10px]">부문 손익 없음</span>
              {sp.note}
            </p>
          )}

          {/* **못 받은 것을 이름으로 적는다.** 목록이 비어야 다 받은 것이다 */}
          {missing.length > 0 ? (
            <p className="rounded-md border border-warn/50 px-2.5 py-2 text-[11.5px] leading-[1.7]">
              <span className="mr-1.5 font-mono text-[10px] text-warn">
                못 받음
              </span>
              {missing.join(" · ")} — 「없다」가 아니라 <strong>조회가 안
              됐다</strong>는 뜻입니다.
            </p>
          ) : (
            info.fiscal_year != null && (
              <Check label="정기보고서 주요정보" ok detail="전 항목 수신" />
            )
          )}
        </div>
      </RailSection>

      {(info.opinion || info.employees != null || val.roe != null) && (
        <RailSection title="공시 요약" count="—">
          <dl className="space-y-1">
            <Fact label="감사의견" value={info.opinion} extra={info.auditor} />
            <Fact
              label="ROE"
              value={val.roe != null ? `${val.roe.toFixed(1)}%` : ""}
            />
            <Fact
              label="부채비율"
              value={val.debt_ratio != null ? `${val.debt_ratio.toFixed(1)}%` : ""}
            />
            <Fact
              label="BPS"
              value={val.bps != null ? `${Math.round(val.bps).toLocaleString()}원` : ""}
            />
            <Fact
              label="주당배당"
              value={val.dps != null ? `${val.dps.toLocaleString()}원` : ""}
              extra={
                val.payout_ratio != null
                  ? `배당성향 ${val.payout_ratio.toFixed(1)}%`
                  : ""
              }
            />
            <Fact
              label="직원"
              value={info.employees != null ? `${info.employees.toLocaleString()}명` : ""}
              extra={
                info.avg_tenure != null ? `평균 근속 ${info.avg_tenure}년` : ""
              }
            />
          </dl>
        </RailSection>
      )}

      {(vm.business?.signals?.length ?? 0) > 0 && (
        <RailSection title="사업 특성" count={vm.business.signals!.length}>
          <ul className="space-y-1">
            {vm.business.signals!.map((sig, i) => (
              <li key={i} className="text-[11.5px] leading-[1.7]">
                {sig}
              </li>
            ))}
          </ul>
          {vm.business.affiliate_weight != null && (
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              출자 장부가가 자산총계의{" "}
              <span className="font-mono">
                {vm.business.affiliate_weight.toFixed(1)}%
              </span>
              입니다.
            </p>
          )}
          {vm.business.source_title && (
            <p className="mt-1 text-[10.5px] text-muted-foreground">
              출처: {vm.business.source_title}
            </p>
          )}
        </RailSection>
      )}
    </div>
  );
}

/** 검산 한 줄. **통과한 것도 보인다** — 안 보이면 안 한 것과 같다. */
function Check({
  label,
  ok,
  detail,
  value,
  why,
}: {
  label: string;
  ok: boolean;
  detail?: string;
  value?: string;
  why?: string;
}) {
  return (
    <div title={why}>
      <div className="flex items-baseline gap-1.5">
        <span className={cn("text-[11px]", ok ? "text-ok" : "text-bad")}>
          {ok ? "✓" : "✕"}
        </span>
        <span className="text-[11.5px]">{label}</span>
        {value && (
          <span
            className={cn(
              "ml-auto font-mono text-[11px] tabular-nums",
              ok ? "text-muted-foreground" : "text-bad",
            )}
          >
            {value}
          </span>
        )}
      </div>
      {detail && (
        <p className="pl-4 text-[10.5px] text-muted-foreground">{detail}</p>
      )}
    </div>
  );
}

function Fact({
  label,
  value,
  extra,
}: {
  label: string;
  value?: string;
  extra?: string;
}) {
  if (!value) return null;
  return (
    <div className="flex items-baseline gap-2">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className="ml-auto text-right">
        <span className="font-mono text-[11.5px] tabular-nums">{value}</span>
        {extra && (
          <span className="ml-1.5 text-[10.5px] text-muted-foreground">
            {extra}
          </span>
        )}
      </dd>
    </div>
  );
}

function countChecks(vm: ViewModel): number {
  const val = vm.valuation ?? {};
  const sp = vm.segment_profit ?? {};
  const info = vm.report_info ?? {};
  let n = 0;
  if (val.eps_stmt != null && val.eps_disclosed != null) n++;
  if (val.shares_issued != null) n++;
  if (sp.reconciled != null && (sp.lines?.length ?? 0) > 0) n++;
  if (info.fiscal_year != null) n++;
  return n;
}

/** 문제가 있으면 **펼친 채로 시작한다.** 접혀 있으면 못 본다. */
function hasProblem(vm: ViewModel): boolean {
  const val = vm.valuation ?? {};
  const sp = vm.segment_profit ?? {};
  return (
    !!vm.info_error ||
    (vm.report_info?.unavailable?.length ?? 0) > 0 ||
    (val.eps_gap_pct != null && Math.abs(val.eps_gap_pct) >= 1) ||
    (val.shares_issued != null && !val.shares_reconciled) ||
    (sp.reconciled === false && (sp.lines?.length ?? 0) > 0)
  );
}
