import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useLiveSocket } from "../hooks/useLiveSocket";

const NAV = [
  { to: "/", label: "Fleet overview", icon: "▦", end: true },
  { to: "/vehicles", label: "Vehicle search", icon: "⌕" },
  { to: "/alerts", label: "Alerts", icon: "⚠" },
  { to: "/risk", label: "Risk & ML", icon: "◆" },
  { to: "/simulation", label: "Simulation", icon: "▶" },
  { to: "/observability", label: "Observability", icon: "◉" },
  { to: "/sql", label: "SQL sandbox", icon: "⌗" },
];

export default function Layout() {
  const { auth, logout } = useAuth();
  const { status, fleetSummary } = useLiveSocket();
  const [open, setOpen] = useState(false);
  const openAlerts = fleetSummary?.active_alerts ?? 0;

  return (
    <div className={`shell ${open ? "nav-open" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">⚡</span>
          <div>
            <div className="brand-name">CVI Platform</div>
            <div className="muted small">Fleet battery intelligence</div>
          </div>
        </div>
        <nav>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className="nav-link" onClick={() => setOpen(false)}>
              <span className="nav-icon">{n.icon}</span>
              <span>{n.label}</span>
              {n.to === "/alerts" && openAlerts > 0 && <span className="nav-count">{openAlerts.toLocaleString()}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className={`live-pill live-${status}`} title="Live updates via /ws/live">
            <span className="dot" /> {status === "open" ? "Live" : status === "connecting" ? "Connecting…" : "Offline"}
          </div>
          <div className="user-box">
            <div className="small ellipsis">{auth?.email}</div>
            <div className="muted small">{auth?.role.replace("_", " ").toLowerCase()}</div>
          </div>
          <button className="btn small block" onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="btn small menu-btn" onClick={() => setOpen((o) => !o)} aria-label="Toggle navigation">
            ☰
          </button>
          <span className="brand-name">CVI Platform</span>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
      {open && <div className="scrim" onClick={() => setOpen(false)} />}
    </div>
  );
}
