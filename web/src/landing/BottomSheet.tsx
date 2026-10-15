import { useEffect, useRef, useState } from "react";
import { PillButton } from "../components/ui";

const CELL = 18;
const GAP = 5;
const COLORS = ["var(--pink)", "var(--amber)", "var(--peri)", "var(--green)", "var(--red)", "var(--yellow)", "var(--plum)"];

/** Closing call-to-action: a field of pixels that light up around the card. */
export default function BottomSheet() {
  const ref = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ cols: 0, rows: 0 });
  const [lit, setLit] = useState<Map<number, string>>(new Map());

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const w = e.contentRect.width, h = e.contentRect.height;
      setDims({ cols: Math.floor((w + GAP) / (CELL + GAP)), rows: Math.floor((h + GAP) / (CELL + GAP)) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const total = dims.cols * dims.rows;
    if (!total) return;
    const shuffle = () => {
      const m = new Map<number, string>();
      const n = Math.max(8, Math.round(total * 0.025));
      for (let k = 0; k < n; k++) m.set(Math.floor(Math.random() * total), COLORS[k % COLORS.length]);
      setLit(m);
    };
    shuffle();
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const t = setInterval(shuffle, 1400);
    return () => clearInterval(t);
  }, [dims]);

  return (
    <section className="sheet">
      <div className="sheet-grid" ref={ref} aria-hidden>
        <div className="sheet-cells" style={{ gridTemplateColumns: `repeat(${dims.cols}, ${CELL}px)` }}>
          {Array.from({ length: dims.cols * dims.rows }, (_, k) => (
            <span key={k} style={lit.has(k) ? { background: lit.get(k) } : undefined} />
          ))}
        </div>
      </div>
      <div className="sheet-card block red">
        <p className="sheet-title">move fresher produce <em>today</em></p>
        <PillButton tone="pink" to="/plan">Plan today's dispatch</PillButton>
      </div>
    </section>
  );
}
