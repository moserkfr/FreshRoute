import { useEffect, useMemo, useState } from "react";
import { useReveal } from "../components/ui";
import type { EvalReport } from "../api/types";
import { useEvalReport } from "../state/useEvalReport";

interface Tile { tone: string; value: number; fmt: (v: number) => string; label: string }

function headline(r: EvalReport): Tile[] {
  const scen = Object.keys(r.aggregate);
  const mean = (f: (s: string) => number) => scen.reduce((a, s) => a + f(s), 0) / scen.length;
  const ours = (s: string) => r.aggregate[s].freshroute;
  const base = (s: string) => r.aggregate[s][r.meta.baseline];
  const spoilOurs = mean((s) => ours(s).spoilage_pct.mean);
  const spoilBase = mean((s) => base(s).spoilage_pct.mean);
  const uplift = mean((s) => ours(s).net_revenue_inr.mean / base(s).net_revenue_inr.mean - 1);
  const win = mean((s) => ours(s).win_rate?.net_revenue_inr ?? 0);
  const viol = mean((s) => ours(s).violations_total.mean);
  const baseViol = mean((s) => base(s).violations_total.mean);
  return [
    { tone: "yellow", value: (1 - spoilOurs / spoilBase) * 100, fmt: (v) => `${v.toFixed(0)}%`, label: `less produce spoiled than today's FIFO dispatch (${(spoilOurs * 100).toFixed(1)}% vs ${(spoilBase * 100).toFixed(1)}%)` },
    { tone: "brown", value: uplift * 100, fmt: (v) => `+${v.toFixed(0)}%`, label: "net revenue over a fortnight, averaged across scenarios" },
    { tone: "peri", value: win * 100, fmt: (v) => `${v.toFixed(0)}%`, label: `of the ${scen.length * r.meta.n_seeds} seed × scenario runs earned more than the baseline` },
    { tone: "red", value: viol, fmt: (v) => v.toFixed(0), label: `constraint violations per run. The baseline averages ${baseViol.toFixed(0)}` },
  ];
}

function CountUp({ to, fmt, run }: { to: number; fmt: (v: number) => string; run: boolean }) {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (!run) return;
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / 1400);
      setV(to * (1 - Math.pow(1 - k, 3)));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [to, run]);
  return <>{fmt(run ? v : 0)}</>;
}

export default function StatTiles() {
  const { report, isSample } = useEvalReport();
  const tiles = useMemo(() => (report ? headline(report) : []), [report]);
  const [ref, shown] = useReveal<HTMLDivElement>();

  return (
    <section className="section stats">
      <div className="wrap">
        <h2 className="h2">fresher produce, measured <em>at scale</em></h2>
        <div ref={ref} className={`stat-row ${shown ? "in" : ""}`}>
          {tiles.map((t, n) => (
            <div key={n} className={`block stat-tile ${t.tone}`} style={{ transitionDelay: `${n * 110}ms` }}>
              <div className="stat-value num"><CountUp to={t.value} fmt={t.fmt} run={shown} /></div>
              <div className="stat-label">{t.label}</div>
            </div>
          ))}
        </div>
        {report && (
          <p className="muted center stat-foot">
            FreshRoute vs {report.meta.baseline === "fifo_nearest" ? "B1 (FIFO to the nearest market)" : report.meta.baseline},{" "}
            {report.meta.seed_label}, {report.meta.scenarios.length} scenarios, {Math.round(report.meta.ci_level * 100)}% bootstrap CIs
            {isSample ? ". Sample report. Final numbers land in reports/final/." : "."}
          </p>
        )}
      </div>
    </section>
  );
}
