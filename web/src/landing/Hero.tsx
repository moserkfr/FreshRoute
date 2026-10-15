import { useEffect, useState } from "react";
import network from "../data/network.json";
import southAsia from "../data/south-asia.json";
import { PillButton } from "../components/ui";

const W = 520;
const R = 250;

// Orthographic projection centred on south India; the disk shows a cap ~14° in radius,
// enough for Sri Lanka, Nepal and the edges of Pakistan and Bangladesh.
const RAD = Math.PI / 180;
const LON0 = 79 * RAD, LAT0 = 15.5 * RAD;
const K = R / Math.sin(14 * RAD);
function proj(p: { lon: number; lat: number }) {
  const lam = p.lon * RAD - LON0, phi = p.lat * RAD;
  return {
    x: W / 2 + K * Math.cos(phi) * Math.sin(lam),
    y: W / 2 - K * (Math.cos(LAT0) * Math.sin(phi) - Math.sin(LAT0) * Math.cos(phi) * Math.cos(lam)),
  };
}
const ringPath = (ring: number[][]) =>
  ring.map(([lon, lat], k) => { const { x, y } = proj({ lon, lat }); return `${k ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`; }).join("") + "Z";
const COUNTRIES = southAsia.countries.map((c) => ({ name: c.name, d: c.rings.map(ringPath).join("") }));
const inDisk = (p: { x: number; y: number }, margin = 24) => Math.hypot(p.x - W / 2, p.y - W / 2) < R - margin;
const PLACE_LABELS = [
  { text: "INDIA", lon: 77.2, lat: 22.4, kind: "country" },
  { text: "SRI LANKA", lon: 84.6, lat: 7.6, kind: "neighbour" },
  { text: "NEPAL", lon: 83.6, lat: 28.6, kind: "neighbour" },
  { text: "PAKISTAN", lon: 68.6, lat: 26.4, kind: "neighbour" },
  { text: "BANGLADESH", lon: 89.6, lat: 24.6, kind: "neighbour" },
  { text: "Bay of Bengal", lon: 87.5, lat: 15.2, kind: "sea" },
  { text: "Arabian Sea", lon: 68.5, lat: 15.5, kind: "sea" },
].map((l) => ({ ...l, ...proj(l) })).filter((l) => inDisk(l));

const hub = proj(network.hub);
// Destinations this close to the hub share the hub's label instead of colliding.
const NEAR = 24;
const near = network.destinations.filter((d) => { const p = proj(d); return Math.hypot(p.x - hub.x, p.y - hub.y) < NEAR; });
const hubLabel = ["Kolar hub", ...new Set(near.map((d) => d.name.split(" ")[0]))].join(" · ");

// What FreshRoute's daily plan does, phrased as the decisions the planner actually makes.
const CARDS = [
  { tag: "ship", title: "Ship tomato to the closest buyer that still pays", body: "Ripe stock goes where it arrives above the buyer's quality floor." },
  { tag: "hold", title: "Hold storable onion for the festival", body: "The demand calendar sees Deepavali coming, so storable stock waits for it." },
  { tag: "reroute", title: "A reefer is down, so replan", body: "Chilled loads move to the other reefer. The plan still has 0 violations." },
  { tag: "discount", title: "Mark down spinach at the mandi", body: "Short-life greens get a discount today instead of spoiling tomorrow." },
];

export default function Hero() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((n) => (n + 1) % CARDS.length), 3600);
    return () => clearInterval(t);
  }, []);

  return (
    <section className="hero">
      <div className="hero-inner wrap">
        <div className="hero-visual" aria-hidden>
          <svg viewBox={`0 0 ${W} ${W}`} className="disk">
            <defs>
              <clipPath id="disk-clip"><circle cx={W / 2} cy={W / 2} r={R} /></clipPath>
            </defs>
            <circle cx={W / 2} cy={W / 2} r={R} fill="var(--green)" />
            <g clipPath="url(#disk-clip)">
              {COUNTRIES.map((c) => (
                <path key={c.name} d={c.d} fillRule="evenodd" className={c.name === "India" ? "land india" : "land"} />
              ))}
              <g fill="none" stroke="rgb(255 255 255 / 0.14)" strokeWidth="1.2">
                {[0.35, 0.7, 1].map((k) => <ellipse key={k} cx={W / 2} cy={W / 2} rx={R * k} ry={R} />)}
                {[-0.6, -0.2, 0.2, 0.6].map((k) => <line key={k} x1="0" x2={W} y1={W / 2 + R * k} y2={W / 2 + R * k} />)}
              </g>
              {PLACE_LABELS.map((l) => (
                <text key={l.text} x={l.x} y={l.y} textAnchor="middle" className={`place-label ${l.kind}`}>{l.text}</text>
              ))}
            </g>
            {network.destinations.map((d, n) => {
              const p = proj(d);
              // Bow each lane sideways by a quarter of its length so short and long lanes curve alike.
              const mx = (hub.x + p.x) / 2, my = (hub.y + p.y) / 2;
              const bend = 0.25;
              const path = `M${hub.x},${hub.y} Q${mx - (p.y - hub.y) * bend},${my + (p.x - hub.x) * bend} ${p.x},${p.y}`;
              const label = d.name.split(" (")[0].replace(" APMC", "");
              const left = p.x < hub.x;
              return (
                <g key={d.id}>
                  <path d={path} fill="none" stroke="var(--yellow)" strokeWidth="2" strokeDasharray="4 5" className="lane" />
                  <circle r="4" fill="var(--yellow)">
                    <animateMotion dur={`${4 + n * 0.7}s`} repeatCount="indefinite" path={path} />
                  </circle>
                  <rect x={p.x - 5} y={p.y - 5} width="10" height="10" rx="2.5" fill="var(--yellow)" stroke="var(--green)" strokeWidth="1.5" />
                  {!near.includes(d) && (
                    <text x={p.x + (left ? -9 : 9)} y={p.y + 4} textAnchor={left ? "end" : "start"} className="disk-label">{label}</text>
                  )}
                </g>
              );
            })}
            <circle cx={hub.x} cy={hub.y} r="8" fill="var(--red)" />
            <circle cx={hub.x} cy={hub.y} r="8" fill="none" stroke="var(--red)" className="pulse-ring" />
            <text x={hub.x} y={hub.y - 16} textAnchor="middle" className="disk-label strong">{hubLabel}</text>
          </svg>
          {CARDS.map((c, n) => (
            <div key={c.tag} className={`hero-card ${n === i ? "on" : ""}`}>
              <div className="hero-card-tag">{c.tag}</div>
              <div className="hero-card-title">{c.title}</div>
              <div className="hero-card-body">{c.body}</div>
            </div>
          ))}
        </div>
        <div className="hero-copy">
          <h1 className="display">perishable dispatch that <em>outruns</em> the shelf life</h1>
          <p className="lede">
            FreshRoute plans each day's trucks from the hub. It decides which batch goes where, on which
            vehicle and at what discount, using the remaining shelf life, live market forecasts and the festival
            calendar. Every decision comes with its reason.
          </p>
          <div className="row" style={{ marginTop: 26 }}>
            <PillButton tone="amber" to="/plan">Plan today's dispatch</PillButton>
            <PillButton tone="ghost" to="/evaluation">See the evidence</PillButton>
          </div>
        </div>
      </div>
      <div className="strip" aria-label="Destinations served">
        <div className="strip-track">
          {[0, 1].map((k) => (
            <div className="strip-set" key={k} aria-hidden={k === 1}>
              {network.destinations.map((d) => (
                <span key={d.id} className="strip-item">
                  <span className={`strip-mark t-${d.type}`} />{d.name}
                </span>
              ))}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
