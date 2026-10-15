import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

export function Arrow({ dir = "right" }: { dir?: "right" | "up" | "down" }) {
  const rot = dir === "up" ? -90 : dir === "down" ? 90 : 0;
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden style={{ transform: `rotate(${rot}deg)` }}>
      <path d="M2 7h9M7.5 3.5 11 7l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

type Tone = "peri" | "amber" | "pink" | "yellow" | "red" | "ghost";

export function PillButton({ children, tone = "peri", to, onClick, disabled, busy, type = "button" }: {
  children: ReactNode; tone?: Tone; to?: string; onClick?: () => void; disabled?: boolean; busy?: boolean;
  type?: "button" | "submit";
}) {
  const cls = `btn ${tone === "peri" ? "" : tone}`;
  const icon = <span className="arrow">{busy ? <span className="spinner" style={{ width: 12, height: 12 }} /> : <Arrow />}</span>;
  if (to) return <Link className={cls} to={to}>{children}{icon}</Link>;
  return <button className={cls} type={type} onClick={onClick} disabled={disabled || busy}>{children}{icon}</button>;
}

export function Kpi({ label, value, sub, tone = "yellow" }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className={`block kpi ${tone}`}>
      <div className="value">{value}</div>
      <div>
        <div className="label">{label}</div>
        {sub != null && <div className="sub">{sub}</div>}
      </div>
    </div>
  );
}

export function ViolationsBadge({ n }: { n: number }) {
  return n === 0
    ? <span className="badge good"><CheckIcon /> 0 violations</span>
    : <span className="badge bad"><WarnIcon /> {n} violation{n === 1 ? "" : "s"}</span>;
}

export function CheckIcon() {
  return <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden><path d="M2 6.5 5 9l5-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}
export function WarnIcon() {
  return <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden><path d="M6 1.5 11 10.5H1z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" /><path d="M6 5v2.3M6 8.8v.2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>;
}

export function Loading({ label }: { label: string }) {
  return <div className="notice info" role="status"><span className="spinner" /> {label}</div>;
}

export function ErrorNotice({ message, onClose }: { message: string; onClose?: () => void }) {
  return (
    <div className="notice bad" role="alert">
      <WarnIcon />
      <span style={{ flex: 1 }}>{message}</span>
      {onClose && <button className="btn ghost plain" onClick={onClose} style={{ padding: "2px 10px" }}>Dismiss</button>}
    </div>
  );
}

/** Adds `.in` once the element scrolls into view. */
export function useReveal<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || shown) return;
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setShown(true); io.disconnect(); } }, { threshold: 0.2 });
    io.observe(el);
    return () => io.disconnect();
  }, [shown]);
  return [ref, shown] as const;
}

/** Floating tooltip that follows the pointer. */
export function useTooltip() {
  const [tip, setTip] = useState<{ x: number; y: number; content: ReactNode } | null>(null);
  const show = (e: { clientX: number; clientY: number }, content: ReactNode) => setTip({ x: e.clientX, y: e.clientY, content });
  const hide = () => setTip(null);
  const node = tip && (
    <div className="tooltip" style={{ left: Math.min(tip.x + 14, window.innerWidth - 270), top: tip.y + 14 }}>{tip.content}</div>
  );
  return { show, hide, node };
}

export function Sparkline({ values, split, width = 120, height = 32, label }: {
  values: number[]; split?: number; width?: number; height?: number; label?: string;
}) {
  if (values.length < 2) return null;
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number) => 2 + (i / (values.length - 1)) * (width - 4);
  const y = (v: number) => height - 3 - ((v - min) / span) * (height - 6);
  const path = (from: number, to: number) => values.slice(from, to).map((v, i) => `${i ? "L" : "M"}${x(from + i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const s = split ?? values.length;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
      <path d={path(0, s)} fill="none" stroke="var(--ink-2)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {s < values.length && (
        <path d={path(s - 1, values.length)} fill="none" stroke="var(--s-freshroute)" strokeWidth="2" strokeDasharray="3 3" strokeLinecap="round" />
      )}
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r="2.5" fill="var(--s-freshroute)" />
    </svg>
  );
}
