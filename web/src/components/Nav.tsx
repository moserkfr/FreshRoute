import { useEffect, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import "./nav.css";

const LINKS = [
  { to: "/plan", label: "Today's plan" },
  { to: "/stock", label: "My stock" },
  { to: "/map", label: "Map" },
  { to: "/market", label: "Market pulse" },
  { to: "/whatif", label: "What-if" },
  { to: "/evaluation", label: "Evaluation" },
  { to: "/settings", label: "Settings" },
];

type Theme = "light" | "dark" | null;

function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => (document.documentElement.dataset.theme as Theme) ?? null);
  useEffect(() => {
    const root = document.documentElement;
    if (theme) root.dataset.theme = theme;
    else delete root.dataset.theme;
    try {
      if (theme) localStorage.setItem("fr.theme", theme);
      else localStorage.removeItem("fr.theme");
    } catch {
      /* ignore */
    }
  }, [theme]);
  const isDark = theme === "dark" || (theme === null && window.matchMedia("(prefers-color-scheme: dark)").matches);
  return { isDark, toggle: () => setTheme(isDark ? "light" : "dark") };
}

export function Logo() {
  return (
    <Link to="/" className="logo" aria-label="FreshRoute home">
      <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden>
        <circle cx="16" cy="16" r="14" fill="var(--green)" />
        <path d="M9 18c4-1 6-5 7-9 1 4 3 8 7 9" fill="none" stroke="var(--yellow)" strokeWidth="2.6" strokeLinecap="round" />
        <circle cx="16" cy="22" r="2.4" fill="var(--yellow)" />
      </svg>
      freshroute
    </Link>
  );
}

export default function Nav() {
  const { isDark, toggle } = useTheme();
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  return (
    <header className="nav-shell">
      <nav className={`nav ${open ? "open" : ""}`} aria-label="Main">
        <Logo />
        <div className="nav-links">
          {LINKS.map((l) => (
            <NavLink key={l.to} to={l.to} className={({ isActive }) => (isActive ? "active" : "")}>{l.label}</NavLink>
          ))}
        </div>
        <div className="nav-right">
          <button className="icon-btn" onClick={toggle} aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}>
            {isDark ? (
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden><circle cx="8" cy="8" r="3.2" fill="currentColor" /><g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">{[0, 45, 90, 135, 180, 225, 270, 315].map((a) => <line key={a} x1="8" y1="1.5" x2="8" y2="3" transform={`rotate(${a} 8 8)`} />)}</g></svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden><path d="M13.5 9.5A5.8 5.8 0 0 1 6.5 2.5a5.8 5.8 0 1 0 7 7Z" fill="currentColor" /></svg>
            )}
          </button>
          <button className="icon-btn burger" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label="Menu">
            <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden><path d={open ? "M4 4l10 10M14 4 4 14" : "M3 5h12M3 9h12M3 13h12"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
          </button>
        </div>
      </nav>
    </header>
  );
}
