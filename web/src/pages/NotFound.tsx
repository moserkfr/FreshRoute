import { PillButton } from "../components/ui";

export default function NotFound() {
  return (
    <div className="page wrap" style={{ display: "grid", placeItems: "center", textAlign: "center" }}>
      <div>
        <h1 className="display">this route <em>spoiled</em></h1>
        <p className="lede center">There's no page at this address.</p>
        <div style={{ display: "flex", justifyContent: "center", marginTop: 24 }}>
          <PillButton tone="amber" to="/">Back to the start</PillButton>
        </div>
      </div>
    </div>
  );
}
