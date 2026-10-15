import { Link } from "react-router-dom";
import { Logo } from "./Nav";
import "./footer.css";

export default function Footer() {
  return (
    <footer className="footer">
      <div className="wrap footer-grid">
        <div>
          <Logo />
          <p className="muted footer-blurb">
            Shelf-life-aware, explainable dispatch planning for perishable produce, from the Kolar hub
            to mandis, retail DCs and processors across South India.
          </p>
        </div>
        <div>
          <p className="eyebrow">Planner</p>
          <Link to="/plan">Today's plan</Link>
          <Link to="/map">Map</Link>
          <Link to="/whatif">What-if</Link>
        </div>
        <div>
          <p className="eyebrow">Insight</p>
          <Link to="/market">Market pulse</Link>
          <Link to="/evaluation">Evaluation</Link>
        </div>
        <div>
          <p className="eyebrow">About</p>
          <Link to="/sources">Assumptions &amp; sources</Link>
          <Link to="/settings">Settings</Link>
          <a href="http://localhost:8000/docs" target="_blank" rel="noreferrer">API docs</a>
        </div>
      </div>
      <div className="wrap footer-base muted">
        <span>Prices come from the fitted market model; there is no live mandi feed.</span>
        <span>Units: ₹ · kg · % · hours</span>
      </div>
    </footer>
  );
}
