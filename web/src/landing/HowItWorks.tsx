import { useEffect, useRef, useState } from "react";

const STEP_MS = 7000;
const VIDEO_SRC = "/media/how-it-works.mp4";

const STEPS = [
  {
    title: "Read the day's stock",
    body: "Each batch at the hub comes with its commodity, grade, quantity and remaining shelf life. The shelf life is a distribution, not a single date, and it ages faster in the heat (Q10 kinetics).",
  },
  {
    title: "Forecast every market",
    body: "The fitted market model forecasts price and demand at each destination, and the festival calendar flags uplift days ahead of time.",
  },
  {
    title: "Plan under shelf-life risk",
    body: "One optimisation picks the destination, truck, reefer setpoint and discount for each batch. The plan has to arrive above each buyer's quality floor with the confidence you set.",
  },
  {
    title: "Explain and replan",
    body: "Every allocation gets a reason: what was chosen, the runner-up and the drivers. If a lane closes or a truck breaks down, the planner replans in seconds and shows what changed.",
  },
];

function Scene({ step }: { step: number }) {
  if (step === 0) {
    const rows = [["Spinach", "A", 22], ["Tomato", "A", 58], ["Okra", "B", 41], ["Mango", "A", 74], ["Onion", "A", 92]] as const;
    return (
      <div className="scene-card">
        <div className="scene-head">Hub stock <span className="scene-chip">kolar_hub</span></div>
        {rows.map(([c, g, life], n) => (
          <div className="scene-batch" key={c} style={{ animationDelay: `${n * 0.18}s` }}>
            <span>{c}</span><span className="scene-grade">grade {g}</span>
            <span className="scene-life"><span style={{ width: `${life}%`, animationDelay: `${0.6 + n * 0.18}s` }} /></span>
          </div>
        ))}
        <div className="scene-note">Bar length shows remaining shelf life</div>
      </div>
    );
  }
  if (step === 1) {
    return (
      <div className="scene-card">
        <div className="scene-head">Market forecast <span className="scene-chip">Bengaluru KR Market</span></div>
        <svg viewBox="0 0 260 90" className="scene-chart" aria-hidden>
          <path d="M5 70 L40 62 L75 66 L110 50 L145 54" fill="none" stroke="currentColor" strokeWidth="2.2" className="draw" />
          <path d="M145 54 L180 40 L215 22 L250 30" fill="none" stroke="var(--red)" strokeWidth="2.2" strokeDasharray="4 4" className="draw late" />
          <line x1="215" x2="215" y1="6" y2="86" stroke="var(--amber)" strokeWidth="1.5" />
        </svg>
        <div className="scene-flag"><span className="dot" style={{ background: "var(--amber)" }} /> Festival ahead: demand uplift expected</div>
        <div className="scene-note">Solid line is observed price, dashed is forecast</div>
      </div>
    );
  }
  if (step === 2) {
    const trucks = [["A5", "Bengaluru KR", 92], ["R2", "Chennai Koyambedu", 78], ["A4", "Malur processing", 64], ["A6", "Mysuru", 85]] as const;
    return (
      <div className="scene-card">
        <div className="scene-head">Today's plan <span className="scene-chip good">0 violations</span></div>
        {trucks.map(([v, d, fill], n) => (
          <div className="scene-truck" key={v} style={{ animationDelay: `${n * 0.2}s` }}>
            <span className="scene-vid">{v}</span>
            <span className="scene-dest">→ {d}</span>
            <span className="scene-fill"><span style={{ width: `${fill}%`, animationDelay: `${0.5 + n * 0.2}s` }} /></span>
          </div>
        ))}
        <div className="scene-note">Bars show how full each truck is</div>
      </div>
    );
  }
  return (
    <div className="scene-card">
      <div className="scene-head">Why this decision</div>
      <p className="scene-why">Spinach goes to <strong>Bengaluru KR Market</strong> on A5. It arrives fresh enough at 90% confidence.</p>
      <div className="scene-driver"><span>price</span><span>forecast ₹/kg at arrival</span></div>
      <div className="scene-driver"><span>freight</span><span>full-truck ₹/kg</span></div>
      <div className="scene-driver"><span>quality</span><span>arrival freshness p10</span></div>
      <div className="scene-event"><span className="dot" style={{ background: "var(--red)" }} /> Reefer R1 breaks down. Replanning…</div>
    </div>
  );
}

export default function HowItWorks() {
  const [active, setActive] = useState(0);
  const [paused, setPaused] = useState(false);
  const [hasVideo, setHasVideo] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  // Auto-advance like a chaptered video; hovering the panel pauses it.
  useEffect(() => {
    if (paused) return;
    const t = setTimeout(() => setActive((a) => (a + 1) % STEPS.length), STEP_MS);
    return () => clearTimeout(t);
  }, [active, paused]);

  // With a real recording, jump to the chapter for the active step.
  useEffect(() => {
    const v = videoRef.current;
    if (!v || !hasVideo || !Number.isFinite(v.duration)) return;
    v.currentTime = (active / STEPS.length) * v.duration;
    void v.play().catch(() => undefined);
  }, [active, hasVideo]);

  return (
    <section className="section how" id="how-it-works">
      <div className="wrap">
        <h2 className="h2">from hub stock to <em>sold fresh</em></h2>
        <p className="lede center" style={{ textAlign: "center" }}>
          How FreshRoute turns a morning's stock into a dispatch plan you can explain to the people who load the trucks.
        </p>
        <div className="how-grid" onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)}>
          <div className="how-steps" role="tablist" aria-label="How it works">
            {STEPS.map((s, n) => (
              <button key={s.title} role="tab" aria-selected={n === active} className={`how-step ${n === active ? "on" : ""}`} onClick={() => setActive(n)}>
                <span className="how-step-title">
                  <span className="how-num num">0{n + 1}</span>{s.title}
                  <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden className="chev"><path d="M3 4.5 6 7.5l3-3" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
                </span>
                {n === active && <span className="how-step-body">{s.body}</span>}
                {n === active && <span className="how-progress"><span key={`${active}-${paused}`} className={paused ? "paused" : ""} style={{ animationDuration: `${STEP_MS}ms` }} /></span>}
              </button>
            ))}
          </div>
          <div className={`how-media tone-${active}`} role="tabpanel" aria-label={STEPS[active].title}>
            <video ref={videoRef} src={VIDEO_SRC} muted playsInline loop preload="metadata"
              onLoadedData={() => setHasVideo(true)} onError={() => setHasVideo(false)}
              className="how-video" hidden={!hasVideo} />
            {!hasVideo && <div className="how-scene" key={active}><Scene step={active} /></div>}
          </div>
        </div>
      </div>
    </section>
  );
}
