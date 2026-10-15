import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Grade, StockBatchIn, StockResponse, StockRow } from "../api/types";
import { CheckIcon, ErrorNotice, Kpi, PillButton, WarnIcon } from "../components/ui";
import network from "../data/network.json";
import { useApp } from "../state/AppState";
import { day, inr, kg, pct } from "../utils/format";
import { parseStockText, type ParsedLine, type ParseProblem } from "../utils/stockText";
import { DEFAULT_SIZES, PACK_UNITS, UNITS, unitKg, unitLabel, type PackUnit, type Unit, type UnitSizes } from "../utils/units";
import "./my-stock.css";

interface Item {
  key: string;
  commodity_id: string;
  qty: number;
  unit: Unit;
  grade: Grade;
  harvest_date: string;
  cost_per_kg: string; // "" = use the typical cost basis
  label: string;
}

const COMMODITIES: { id: string; name: string }[] = network.commodities;
const STORE = "fr.stock";
const SIZES_STORE = "fr.unitSizes";
const GRADES: Grade[] = ["A", "B", "C"];
const CSV_HEAD = "commodity,qty,unit,grade,harvest_date,cost_per_kg,lot";

function isoToday(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function shift(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

let seq = 0;
const newKey = () => `k${Date.now().toString(36)}${(seq++).toString(36)}`;

function readStore(): { date: string; items: Item[] } {
  try {
    const raw = localStorage.getItem(STORE);
    if (raw) {
      const v = JSON.parse(raw);
      if (typeof v.date === "string" && Array.isArray(v.items)) {
        // lists saved before units existed carry qty_kg only
        const items = v.items.map((x: Item & { qty_kg?: number }) =>
          x.unit ? x : { ...x, qty: x.qty_kg ?? 0, unit: "kg" as Unit });
        return { date: v.date, items };
      }
    }
  } catch {
    /* fall through */
  }
  return { date: isoToday(), items: [] };
}

function readSizes(): UnitSizes {
  try {
    const raw = localStorage.getItem(SIZES_STORE);
    if (raw) return { ...DEFAULT_SIZES, ...JSON.parse(raw) };
  } catch {
    /* fall through */
  }
  return DEFAULT_SIZES;
}

function example(date: string): Item[] {
  const rows: [string, number, Unit, Grade, number, string, string][] = [
    ["tomato", 120, "crate", "A", 0, "", "Tomato · Mulbagal"],
    ["tomato", 60, "crate", "B", 1, "", "Tomato · Srinivaspur"],
    ["mango", 2.5, "tonne", "A", 2, "38", "Mango · Banganapalli"],
    ["onion", 80, "bag", "A", 1, "", "Onion · store 2"],
    ["cabbage", 1500, "kg", "A", 0, "", "Cabbage"],
    ["spinach", 700, "bundle", "A", 0, "", "Spinach · Kolar"],
    ["okra", 6, "quintal", "B", 1, "", "Okra"],
  ];
  return rows.map(([c, q, u, g, age, cost, label]) => ({
    key: newKey(), commodity_id: c, qty: q, unit: u, grade: g, harvest_date: shift(date, -age), cost_per_kg: cost, label,
  }));
}

function toBatch(it: Item, sizes: UnitSizes): StockBatchIn {
  const cost = it.cost_per_kg.trim() === "" ? null : Number(it.cost_per_kg);
  return {
    commodity_id: it.commodity_id, qty_kg: Math.round(it.qty * unitKg(sizes, it.commodity_id, it.unit) * 10) / 10, grade: it.grade,
    harvest_date: it.harvest_date || null, cost_per_kg: cost != null && Number.isFinite(cost) ? cost : null,
    label: it.label.trim() || null,
  };
}

/** CSV → items. Accepts commodity ids or names, any column order, header required. */
function parseCsv(text: string, date: string): { items: Item[]; errors: string[] } {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const errors: string[] = [];
  if (lines.length < 2) return { items: [], errors: ["The file needs a header row and at least one batch."] };
  const head = lines[0].split(",").map((h) => h.trim().toLowerCase());
  const col = (names: string[]) => head.findIndex((h) => names.includes(h));
  const ci = col(["commodity", "commodity_id"]), qi = col(["qty", "qty_kg", "kg", "quantity"]), ui = col(["unit", "units"]);
  const gi = col(["grade"]), hi = col(["harvest_date", "harvested", "harvest"]);
  const pi = col(["cost_per_kg", "cost", "paid"]), li = col(["lot", "label", "name"]);
  if (ci < 0 || qi < 0) return { items: [], errors: [`The header must include "commodity" and "qty" (got: ${lines[0]}).`] };
  const byName = new Map(COMMODITIES.flatMap((c) => [[c.id, c.id], [c.name.toLowerCase(), c.id]] as [string, string][]));
  const items: Item[] = [];
  lines.slice(1).forEach((line, n) => {
    const f = line.split(",").map((x) => x.trim());
    const at = `Line ${n + 2}`;
    const cid = byName.get((f[ci] ?? "").toLowerCase());
    const qty = Number(f[qi]);
    const grade = ((gi >= 0 ? f[gi] : "") || "A").toUpperCase() as Grade;
    const harvest = (hi >= 0 ? f[hi] : "") || date;
    const unitRaw = ((ui >= 0 ? f[ui] : "") || "kg").toLowerCase();
    const unit = UNITS.find((u) => u === unitRaw || `${u}s` === unitRaw || (u === "box" && unitRaw === "boxes"));
    if (!cid) { errors.push(`${at}: unknown commodity "${f[ci] ?? ""}".`); return; }
    if (!(qty > 0)) { errors.push(`${at}: quantity must be a positive number.`); return; }
    if (!unit) { errors.push(`${at}: unit must be one of ${UNITS.join(", ")}.`); return; }
    if (!GRADES.includes(grade)) { errors.push(`${at}: grade must be A, B or C.`); return; }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(harvest)) { errors.push(`${at}: harvest date must look like 2026-10-14.`); return; }
    items.push({ key: newKey(), commodity_id: cid, qty, unit, grade, harvest_date: harvest,
      cost_per_kg: pi >= 0 ? f[pi] ?? "" : "", label: li >= 0 ? f[li] ?? "" : "" });
  });
  return { items, errors };
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

const STATUS: Record<StockRow["status"], { cls: string; text: string }> = {
  fresh: { cls: "good", text: "Fresh" },
  sell_today: { cls: "warn", text: "Sell today" },
  spoiled: { cls: "bad", text: "Past saleable" },
};

function LifeBar({ row }: { row?: StockRow }) {
  if (!row) return <span className="muted">—</span>;
  const width = Math.max(0, Math.min(1, row.quality));
  return (
    <div className="life">
      <div className={`life-track ${STATUS[row.status].cls}`}><span style={{ width: `${width * 100}%` }} /></div>
      <span className="num life-text">{row.status === "spoiled" ? "0 d" : `${row.days_left_at_hub.toFixed(1)} d`}</span>
    </div>
  );
}

export default function MyStock() {
  const { planStock, busy, source, settings } = useApp();
  const navigate = useNavigate();
  const [store, setStore] = useState(readStore);
  const { date, items } = store;
  const [preview, setPreview] = useState<StockResponse | null>(null);
  const [checking, setChecking] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [csvErrors, setCsvErrors] = useState<string[]>([]);
  const [draft, setDraft] = useState<Omit<Item, "key">>({
    commodity_id: COMMODITIES[0]?.id ?? "tomato", qty: 50, unit: "crate", grade: "A", harvest_date: date, cost_per_kg: "", label: "",
  });
  const [sizes, setSizes] = useState<UnitSizes>(readSizes);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [parsed, setParsed] = useState<{ items: ParsedLine[]; problems: ParseProblem[] } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const setItems = (f: (xs: Item[]) => Item[]) => setStore((s) => ({ ...s, items: f(s.items) }));
  const setDate = (d: string) => setStore((s) => ({ ...s, date: d }));
  const update = (key: string, patch: Partial<Item>) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, ...patch } : x)));

  useEffect(() => {
    try {
      localStorage.setItem(SIZES_STORE, JSON.stringify(sizes));
    } catch {
      /* ignore */
    }
  }, [sizes]);

  useEffect(() => {
    try {
      localStorage.setItem(STORE, JSON.stringify(store));
    } catch {
      /* private mode: the list just won't persist */
    }
  }, [store]);

  // Ask the API how each batch enters the plan (life left, status) as the list changes.
  const body = useMemo(() => items.map((it) => toBatch(it, sizes)), [items, sizes]);
  useEffect(() => {
    if (body.length === 0) { setPreview(null); setErr(null); return; }
    let live = true;
    const t = setTimeout(() => {
      setChecking(true);
      api.stockState({ batches: body, plan_date: date, forecast_mode: settings.forecastMode, risk_p: settings.riskP })
        .then((r) => { if (live) { setPreview(r); setErr(null); } })
        .catch((e) => { if (live) { setPreview(null); setErr((e as Error).message); } })
        .finally(() => live && setChecking(false));
    }, 350);
    return () => { live = false; clearTimeout(t); };
  }, [body, date, settings.forecastMode, settings.riskP]);

  // rows line up with items by position; ignore a preview that is one edit behind
  const rows = preview && preview.rows.length === items.length ? preview.rows : [];
  const total = body.reduce((n, b) => n + (b.qty_kg || 0), 0);
  const kgBy = (st: StockRow["status"]) => rows.filter((r) => r.status === st).reduce((n, r) => n + r.qty_kg, 0);
  const plannable = rows.filter((r) => r.planned).length;
  const commodityName = (id: string) => COMMODITIES.find((c) => c.id === id)?.name ?? id;

  function add() {
    if (!(draft.qty > 0)) return;
    setItems((xs) => [...xs, { ...draft, key: newKey() }]);
    setDraft((d) => ({ ...d, label: "" }));
  }

  function readPaste() {
    setParsed(parseStockText(pasteText, COMMODITIES.map((c) => c.id)));
  }

  function addParsed() {
    if (!parsed) return;
    setItems((xs) => [...xs, ...parsed.items.map((p) => ({
      key: newKey(), commodity_id: p.commodity_id, qty: p.qty, unit: p.unit, grade: p.grade,
      harvest_date: shift(date, -p.age_days), cost_per_kg: p.cost_per_kg != null ? String(p.cost_per_kg) : "", label: "",
    }))]);
    setParsed(null);
    setPasteText("");
    setPasteOpen(false);
  }

  async function onFile(f: File | undefined) {
    if (!f) return;
    const { items: parsed, errors } = parseCsv(await f.text(), date);
    setCsvErrors(errors);
    if (parsed.length) setItems((xs) => [...xs, ...parsed]);
    if (fileRef.current) fileRef.current.value = "";
  }

  async function plan() {
    if (!preview || plannable === 0) return;
    await planStock(preview);
    navigate("/plan");
  }

  return (
    <div className="page wrap">
      <header className="page-head">
        <p className="eyebrow">My stock</p>
        <h1 className="h2">plan <em>your own</em> stock</h1>
        <p>
          List what's at the hub: commodity, kilos, grade and the day it was picked. FreshRoute works out how much life
          each batch has left and plans where it should go today, next to B1, today's FIFO-to-nearest practice. The list
          is saved in this browser.
        </p>
      </header>

      <section className="card stock-add">
        <div className="row" style={{ alignItems: "flex-end" }}>
          <label className="field" style={{ flex: "1 1 150px" }}>Commodity
            <select className="select" value={draft.commodity_id} onChange={(e) => setDraft({ ...draft, commodity_id: e.target.value })}>
              {COMMODITIES.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label className="field" style={{ width: 110 }}>Quantity
            <input className="input num" type="number" min={0} step="any" value={draft.qty}
              onChange={(e) => setDraft({ ...draft, qty: Number(e.target.value) })} />
          </label>
          <label className="field" style={{ width: 110 }}>Unit
            <select className="select" value={draft.unit} onChange={(e) => setDraft({ ...draft, unit: e.target.value as Unit })}>
              {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
            </select>
          </label>
          <label className="field" style={{ width: 90 }}>Grade
            <select className="select" value={draft.grade} onChange={(e) => setDraft({ ...draft, grade: e.target.value as Grade })}>
              {GRADES.map((g) => <option key={g}>{g}</option>)}
            </select>
          </label>
          <label className="field" style={{ width: 160 }}>Harvested
            <input className="input num" type="date" max={date} value={draft.harvest_date}
              onChange={(e) => setDraft({ ...draft, harvest_date: e.target.value })} />
          </label>
          <label className="field" style={{ width: 120 }}>Paid (₹/kg)
            <input className="input num" type="number" min={0} step={0.5} placeholder="typical" value={draft.cost_per_kg}
              onChange={(e) => setDraft({ ...draft, cost_per_kg: e.target.value })} />
          </label>
          <label className="field" style={{ flex: "1 1 150px" }}>Lot name <span className="muted">(optional)</span>
            <input className="input" maxLength={24} placeholder="e.g. Tomato · Mulbagal" value={draft.label}
              onChange={(e) => setDraft({ ...draft, label: e.target.value })}
              onKeyDown={(e) => e.key === "Enter" && add()} />
          </label>
          <PillButton tone="amber" onClick={add} disabled={!(draft.qty > 0)}>Add batch</PillButton>
        </div>
        {draft.unit !== "kg" && draft.qty > 0 && (
          <p className="muted unit-hint num">= {kg(draft.qty * unitKg(sizes, draft.commodity_id, draft.unit))} at {unitKg(sizes, draft.commodity_id, draft.unit)} kg per {draft.unit}</p>
        )}
        <div className="row stock-tools">
          <label className="field date-field">Planning day
            <input className="input num" type="date" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} />
          </label>
          <div className="spacer" />
          <input ref={fileRef} type="file" accept=".csv,text/csv" className="sr-only" onChange={(e) => onFile(e.target.files?.[0])} />
          <button className={`btn plain ${pasteOpen ? "yellow" : "ghost"}`} onClick={() => setPasteOpen((o) => !o)} aria-expanded={pasteOpen}>Paste from WhatsApp</button>
          <button className="btn ghost plain" onClick={() => fileRef.current?.click()}>Upload CSV</button>
          <button className="btn ghost plain" onClick={() => download("freshroute-stock-template.csv",
            `${CSV_HEAD}\ntomato,120,crate,A,${date},,Tomato lot 1\nmango,2500,kg,A,${shift(date, -2)},38,Mango lot 1\nonion,60,bag,B,${shift(date, -1)},,\n`)}>CSV template</button>
          <button className="btn ghost plain" onClick={() => setItems((xs) => [...xs, ...example(date)])}>Load example</button>
          {items.length > 0 && <button className="btn ghost plain" onClick={() => { setItems(() => []); setCsvErrors([]); }}>Clear all</button>}
        </div>
        <details className="unit-sizes">
          <summary>Unit sizes <span className="muted">· kg per crate, bag, box and bundle. Typical values; change them to match yours.</span></summary>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Commodity</th>{PACK_UNITS.map((u) => <th key={u} className="r">kg per {u}</th>)}</tr></thead>
              <tbody>
                {COMMODITIES.map((c) => (
                  <tr key={c.id}>
                    <td>{c.name}</td>
                    {PACK_UNITS.map((u: PackUnit) => (
                      <td key={u} className="r">
                        <input className="input sm num size-input" type="number" min={0.1} step="any" aria-label={`kg per ${u} of ${c.name}`}
                          value={unitKg(sizes, c.id, u)}
                          onChange={(e) => {
                            const v = Number(e.target.value);
                            if (v > 0) setSizes((z) => ({ ...z, [c.id]: { ...DEFAULT_SIZES[c.id], ...z[c.id], [u]: v } }));
                          }} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="btn ghost plain" style={{ marginTop: 10 }} onClick={() => setSizes(DEFAULT_SIZES)}>Reset to typical sizes</button>
        </details>
      </section>

      {pasteOpen && (
        <section className="block peri paste">
          <div className="paste-grid">
            <div>
              <p className="eyebrow" style={{ color: "var(--on-light)", opacity: 0.7 }}>paste from whatsapp</p>
              <p className="paste-title">copy the stock message, <em>paste it here</em></p>
              <p className="paste-help">
                One item per line, the way it's usually typed: <span className="num">Tomato 120 crates A</span>,{" "}
                <span className="num">eerulli 60 bags yesterday</span>, <span className="num">Mango 2.5 ton @ 38</span>.
                It understands common English, Hindi and Kannada names, crates, bags, boxes, bundles, quintals and tonnes,
                grades, "today / yesterday / 2 days old" and prices. Chat timestamps and names are ignored. Nothing leaves
                this page until you add the rows.
              </p>
            </div>
            <div className="paste-input">
              <textarea className="textarea" rows={7} value={pasteText} onChange={(e) => { setPasteText(e.target.value); setParsed(null); }}
                placeholder={"[15/10, 9:14 am] Ravi: Tomato 120 crates A grade\ntamatar 40 box B kal\nEerulli 60 bags @ 9\nMango 2.5 ton 2 days old"} aria-label="WhatsApp message" />
              <div className="row" style={{ marginTop: 8 }}>
                <div className="spacer" />
                <button className="btn ghost plain on-peri" onClick={() => { setPasteOpen(false); setParsed(null); }}>Cancel</button>
                <PillButton tone="yellow" onClick={readPaste} disabled={!pasteText.trim()}>Read message</PillButton>
              </div>
            </div>
          </div>

          {parsed && (
            <div className="card paste-result">
              {parsed.items.length === 0 ? (
                <p className="muted" style={{ margin: 0 }}>No stock lines found. Each line needs a commodity name and a quantity.</p>
              ) : (
                <ul className="paste-list">
                  {parsed.items.map((p, i) => (
                    <li key={i}>
                      <span className="badge good"><CheckIcon /> read</span>
                      <span className="paste-read num">
                        {commodityName(p.commodity_id)} · {p.qty.toLocaleString("en-IN")} {unitLabel(p.unit, p.qty)}
                        {p.unit !== "kg" && <span className="muted"> ({kg(p.qty * unitKg(sizes, p.commodity_id, p.unit))})</span>}
                        {" "}· grade {p.grade} · {p.age_days === 0 ? "today" : p.age_days === 1 ? "yesterday" : `${p.age_days} days old`}
                        {p.cost_per_kg != null && <> · paid ₹{p.cost_per_kg}/kg</>}
                      </span>
                      {p.unit_assumed && <span className="badge warn"><WarnIcon /> no unit, read as kg</span>}
                      <span className="muted paste-src">{p.line}</span>
                    </li>
                  ))}
                </ul>
              )}
              {parsed.problems.length > 0 && (
                <ul className="paste-list">
                  {parsed.problems.map((p, i) => (
                    <li key={i}>
                      <span className="badge bad"><WarnIcon /> skipped</span>
                      <span className="paste-read">{p.why}</span>
                      <span className="muted paste-src">{p.line}</span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="row" style={{ marginTop: 10 }}>
                <span className="muted" style={{ fontSize: 13 }}>Check the rows, then add them. You can still edit each one in the table.</span>
                <div className="spacer" />
                <PillButton tone="amber" onClick={addParsed} disabled={parsed.items.length === 0}>
                  {`Add ${parsed.items.length} batch${parsed.items.length === 1 ? "" : "es"}`}
                </PillButton>
              </div>
            </div>
          )}
        </section>
      )}

      {csvErrors.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <ErrorNotice message={`Skipped ${csvErrors.length} CSV line${csvErrors.length === 1 ? "" : "s"}: ${csvErrors.slice(0, 4).join(" ")}${csvErrors.length > 4 ? " …" : ""}`}
            onClose={() => setCsvErrors([])} />
        </div>
      )}
      {err && <div style={{ marginTop: 12 }}><ErrorNotice message={err} /></div>}

      {items.length === 0 ? (
        <div className="card empty" style={{ marginTop: 12 }}>
          <p className="h3" style={{ color: "var(--ink)" }}>no stock <em>yet</em></p>
          <p>Add a batch above, upload a CSV, or load an example hub morning to see how it works.</p>
          <div style={{ display: "flex", justifyContent: "center", marginTop: 12 }}>
            <PillButton tone="ghost" onClick={() => setItems(() => example(date))}>Load example</PillButton>
          </div>
        </div>
      ) : (
        <>
          <div className="kpis" style={{ marginTop: 12 }}>
            <Kpi tone="yellow" label="Stock at the hub" value={kg(total)} sub={`${items.length} batch${items.length === 1 ? "" : "es"} · ${day(date)}`} />
            <Kpi tone="green" label="Fresh" value={kg(kgBy("fresh"))} sub="more than a day of life left" />
            <Kpi tone="amber" label="Sell today" value={kg(kgBy("sell_today"))} sub="under a day left at the hub" />
            <Kpi tone="red" label="Past saleable" value={kg(kgBy("spoiled"))} sub="left out of the plan" />
          </div>

          <div className="card flush" style={{ marginTop: 12 }}>
            <div className="card-title row">
              <span className="h3">your batches</span>
              <span className="muted num">{checking ? <><span className="spinner" style={{ width: 12, height: 12 }} /> checking…</> : `${items.length} rows`}</span>
            </div>
            <div className="table-wrap">
              <table className="table stock-table">
                <thead>
                  <tr>
                    <th>Lot</th><th>Commodity</th><th>Grade</th><th className="r">Quantity</th><th>Harvested</th>
                    <th>Life left at hub</th><th className="r">Quality</th><th className="r">Paid ₹/kg</th><th>Status</th><th aria-label="Remove" />
                  </tr>
                </thead>
                <tbody>
                  {items.map((it, i) => {
                    const row = rows[i];
                    const st = row ? STATUS[row.status] : null;
                    return (
                      <tr key={it.key} className={row?.status === "spoiled" ? "spoiled" : ""}>
                        <td>
                          <input className="input sm" maxLength={24} placeholder={row?.batch_id ?? `MY-${String(i + 1).padStart(3, "0")}`}
                            value={it.label} onChange={(e) => update(it.key, { label: e.target.value })} aria-label="Lot name" />
                        </td>
                        <td>
                          <select className="select sm" value={it.commodity_id} onChange={(e) => update(it.key, { commodity_id: e.target.value })} aria-label="Commodity">
                            {COMMODITIES.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                          </select>
                        </td>
                        <td>
                          <select className="select sm grade" value={it.grade} onChange={(e) => update(it.key, { grade: e.target.value as Grade })} aria-label="Grade">
                            {GRADES.map((g) => <option key={g}>{g}</option>)}
                          </select>
                        </td>
                        <td className="r">
                          <div className="qty-cell">
                            <input className="input sm num qty" type="number" min={0} step="any" value={it.qty}
                              onChange={(e) => update(it.key, { qty: Number(e.target.value) })} aria-label="Quantity" />
                            <select className="select sm unit" value={it.unit} onChange={(e) => update(it.key, { unit: e.target.value as Unit })} aria-label="Unit">
                              {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
                            </select>
                          </div>
                          {it.unit !== "kg" && <span className="muted age num">= {kg(body[i]?.qty_kg)}</span>}
                        </td>
                        <td>
                          <input className="input sm num" type="date" max={date} value={it.harvest_date}
                            onChange={(e) => update(it.key, { harvest_date: e.target.value })} aria-label="Harvest date" />
                          {row && <span className="muted age">{row.age_days === 0 ? "today" : `${row.age_days} d ago`}</span>}
                        </td>
                        <td><LifeBar row={row} /></td>
                        <td className="r">{row ? pct(row.quality, 0) : "—"}</td>
                        <td className="r">
                          <input className="input sm num paid" type="number" min={0} step={0.5}
                            placeholder={row?.cost_assumed ? row.cost_per_kg.toFixed(1) : "typical"}
                            value={it.cost_per_kg} onChange={(e) => update(it.key, { cost_per_kg: e.target.value })} aria-label="Paid per kg" />
                        </td>
                        <td>
                          {st && (
                            <span className={`badge ${st.cls}`}>
                              {row?.status === "fresh" ? <CheckIcon /> : <WarnIcon />} {st.text}
                            </span>
                          )}
                        </td>
                        <td>
                          <button className="row-remove" onClick={() => setItems((xs) => xs.filter((x) => x.key !== it.key))}
                            aria-label={`Remove ${it.label || commodityName(it.commodity_id)}`}>×</button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div className="block plum stock-go">
            <div>
              <p className="eyebrow" style={{ color: "var(--pink)" }}>ready to plan</p>
              <p className="stock-go-text">
                {plannable} of {items.length} batches can still be sold.
                {preview && preview.excluded_kg > 0 && <> {kg(preview.excluded_kg)} is past saleable and is left out.</>}
                {" "}Typical cost basis is assumed where you didn't enter a price
                {rows.some((r) => r.cost_assumed) ? ` (e.g. ${inr(rows.find((r) => r.cost_assumed)!.cost_per_kg)}/kg)` : ""}.
              </p>
              <p className="stock-go-note">
                Life left assumes the stock sits at the hub at {preview ? `${preview.hub_temp_c} °C` : "hub temperature"} since harvest.
                Trucks, markets and forecasts come from the {preview?.base ?? "normal scenario world"}.
              </p>
            </div>
            <PillButton tone="yellow" onClick={plan} busy={busy === "plan"} disabled={!preview || plannable === 0 || checking}>
              {busy === "plan" ? "Planning…" : source === "stock" ? "Plan again" : "Plan my stock"}
            </PillButton>
          </div>
        </>
      )}
    </div>
  );
}
