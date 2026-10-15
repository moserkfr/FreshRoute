import { useMemo, useState } from "react";
import sources from "../data/sources.json";
import "./sources.css";

const TONES = ["yellow", "peri", "pink", "amber", "green"];

export default function Sources() {
  const [q, setQ] = useState("");
  const [onlyAssumptions, setOnlyAssumptions] = useState(false);

  const files = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return sources.map((f) => ({
      ...f,
      rows: f.rows.filter((r) =>
        (!onlyAssumptions || r.text.toLowerCase().startsWith("assumption")) &&
        (!needle || `${r.section} ${r.param} ${r.text}`.toLowerCase().includes(needle))),
    }));
  }, [q, onlyAssumptions]);

  const total = sources.reduce((n, f) => n + f.rows.length, 0);
  const assumptions = sources.reduce((n, f) => n + f.rows.filter((r) => r.text.toLowerCase().startsWith("assumption")).length, 0);

  return (
    <div className="page wrap">
      <header className="page-head">
        <p className="eyebrow">Assumptions &amp; sources</p>
        <h1 className="h2">where every number <em>comes from</em></h1>
        <p>
          Every parameter section in <code>config/*.yaml</code> cites a source or states its assumption. This page is
          generated from those files ({total} entries, {assumptions} of them stated assumptions).
        </p>
      </header>

      <div className="row" style={{ marginBottom: 16 }}>
        <input className="input" style={{ maxWidth: 360 }} placeholder="Filter, e.g. freight, Q10, Agmarknet" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter sources" />
        <label className="row" style={{ gap: 6, fontSize: 14 }}>
          <input type="checkbox" checked={onlyAssumptions} onChange={(e) => setOnlyAssumptions(e.target.checked)} /> Assumptions only
        </label>
      </div>

      <div className="src-grid">
        {files.map((f, k) => (
          <section key={f.file} className="card flush src-file">
            <header className={`block ${TONES[k % TONES.length]} src-head`}>
              <span className="h3">{f.file.replace("config/", "")}</span>
              <span className="num" style={{ fontSize: 13 }}>{f.rows.length} entries</span>
            </header>
            {f.rows.length === 0 ? <p className="empty">No matches.</p> : (
              <dl className="src-list">
                {f.rows.map((r, n) => (
                  <div key={n} className="src-row">
                    <dt><span className="muted">{r.section}</span> <strong>{r.param}</strong></dt>
                    <dd>
                      {r.text.toLowerCase().startsWith("assumption") && <span className="badge warn" style={{ marginRight: 6 }}>assumption</span>}
                      {r.text.split(/(https?:\/\/\S+)/).map((part, i) =>
                        /^https?:\/\//.test(part) ? <a key={i} href={part} target="_blank" rel="noreferrer">{part}</a> : part)}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
        ))}
      </div>
    </div>
  );
}
