import type { ReactNode } from "react";
import type { Stat, TornadoRow } from "../api/types";
import { PLANNER_LABEL } from "../utils/format";
import { useTooltip } from "./ui";

export const seriesColor = (planner: string) => `var(--s-${planner})`;

/**
 * Horizontal bars with CI whiskers, one row per planner, on a scale shared by every
 * scenario panel so magnitudes compare across panels.
 */
export function CiBars({ rows, domain, fmt, title }: {
  rows: { planner: string; stat: Stat }[];
  domain: [number, number];
  fmt: (v: number) => string;
  title: ReactNode;
}) {
  const tip = useTooltip();
  const W = 340, rowH = 22, padL = 4, padR = 64, top = 4;
  const H = top + rows.length * rowH + 4;
  const [lo, hi] = domain;
  const x = (v: number) => padL + ((v - lo) / (hi - lo || 1)) * (W - padL - padR);
  const zero = x(Math.max(lo, Math.min(hi, 0)));

  return (
    <figure className="ci-panel">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={typeof title === "string" ? title : undefined}>
        <line x1={zero} x2={zero} y1={0} y2={H} stroke="var(--line)" />
        {rows.map(({ planner, stat }, k) => {
          const y = top + k * rowH;
          const x0 = Math.min(zero, x(stat.mean)), x1 = Math.max(zero, x(stat.mean));
          const r = Math.min(4, (x1 - x0) / 2);
          return (
            <g key={planner}
              onMouseMove={(e) => tip.show(e, <>
                <strong>{PLANNER_LABEL[planner] ?? planner}</strong><br />
                mean {fmt(stat.mean)}<br />95% CI {fmt(stat.ci_lo)} to {fmt(stat.ci_hi)}<br />n = {stat.n} seeds
              </>)}
              onMouseLeave={tip.hide}
            >
              <rect x={0} y={y} width={W} height={rowH} fill="transparent" />
              <path
                d={stat.mean >= 0
                  ? `M${x0},${y + 5} H${x1 - r} Q${x1},${y + 5} ${x1},${y + 5 + r} V${y + 17 - r} Q${x1},${y + 17} ${x1 - r},${y + 17} H${x0} Z`
                  : `M${x1},${y + 5} H${x0 + r} Q${x0},${y + 5} ${x0},${y + 5 + r} V${y + 17 - r} Q${x0},${y + 17} ${x0 + r},${y + 17} H${x1} Z`}
                fill={seriesColor(planner)}
                opacity={planner === "freshroute" ? 1 : 0.85}
              />
              <line x1={x(stat.ci_lo)} x2={x(stat.ci_hi)} y1={y + 11} y2={y + 11} stroke="var(--ink)" strokeWidth="1.5" />
              <line x1={x(stat.ci_lo)} x2={x(stat.ci_lo)} y1={y + 7} y2={y + 15} stroke="var(--ink)" strokeWidth="1.5" />
              <line x1={x(stat.ci_hi)} x2={x(stat.ci_hi)} y1={y + 7} y2={y + 15} stroke="var(--ink)" strokeWidth="1.5" />
              <text x={W - padR + 6} y={y + 15} fontSize="11" fill="var(--ink-2)" className="num">{fmt(stat.mean)}</text>
            </g>
          );
        })}
      </svg>
      {tip.node}
    </figure>
  );
}

/** Sensitivity tornado: Δ net revenue vs baseline when each parameter is scaled low / high. */
export function Tornado({ rows, base, fmt, lowLabel, highLabel }: {
  rows: TornadoRow[]; base: number; fmt: (v: number) => string; lowLabel: string; highLabel: string;
}) {
  const tip = useTooltip();
  const sorted = [...rows].sort((a, b) => b.swing_net_revenue_inr - a.swing_net_revenue_inr);
  const vals = sorted.flatMap((r) => [r.low.d_net_revenue_inr, r.high.d_net_revenue_inr]).concat(base);
  const span = Math.max(...vals.map((v) => Math.abs(v - base))) || 1;
  const W = 640, labelW = 120, valW = 70, rowH = 30, top = 22;
  const H = top + sorted.length * rowH + 8;
  const mid = labelW + valW + (W - labelW - 2 * valW) / 2;
  const half = (W - labelW - 2 * valW) / 2;
  const x = (v: number) => mid + ((v - base) / span) * half;

  return (
    <div className="tornado">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Sensitivity tornado chart">
        <text x={mid} y={12} textAnchor="middle" fontSize="11" fill="var(--muted)">base {fmt(base)}</text>
        <line x1={mid} x2={mid} y1={top - 4} y2={H} stroke="var(--ink-2)" />
        {sorted.map((r, k) => {
          const y = top + k * rowH;
          const bar = (v: number, color: string, which: string, factor: number) => {
            const a = Math.min(x(v), mid), b = Math.max(x(v), mid);
            return (
              <rect x={a} y={y + 6} width={Math.max(1, b - a)} height={rowH - 12} rx={3} fill={color}
                onMouseMove={(e) => tip.show(e, <><strong>{r.param}</strong> × {factor} ({which})<br />Δ net revenue vs baseline {fmt(v)}<br />{fmt(v - base)} vs base · n = {r.low.n}</>)}
                onMouseLeave={tip.hide} />
            );
          };
          return (
            <g key={r.param}>
              <text x={labelW - 8} y={y + rowH / 2 + 4} textAnchor="end" fontSize="12" fill="var(--ink)">{r.param.replace(/_/g, " ")}</text>
              {bar(r.low.d_net_revenue_inr, "var(--s-fefo_greedy)", lowLabel, r.low_factor)}
              {bar(r.high.d_net_revenue_inr, "var(--s-chase_price)", highLabel, r.high_factor)}
              <text x={W - 4} y={y + rowH / 2 + 4} textAnchor="end" fontSize="11" fill="var(--ink-2)" className="num">swing {fmt(r.swing_net_revenue_inr)}</text>
            </g>
          );
        })}
      </svg>
      <div className="legend" style={{ marginTop: 8 }}>
        <span><span className="dot" style={{ background: "var(--s-fefo_greedy)" }} />{lowLabel}</span>
        <span><span className="dot" style={{ background: "var(--s-chase_price)" }} />{highLabel}</span>
      </div>
      {tip.node}
    </div>
  );
}
