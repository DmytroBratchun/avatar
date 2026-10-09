import ClaudeUsage from "./components/ClaudeUsage";
import { useEffect, useRef, useState } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import "./App.css";
import LibraryPage from "./pages/LibraryPage";
import LogsPage from "./pages/LogsPage";
import SearchPage from "./pages/SearchPage";
import SceneDetailPage from "./pages/SceneDetailPage";
import ScenesPage from "./pages/ScenesPage";
import SourceDetailPage from "./pages/SourceDetailPage";
import DatabasePage from "./pages/DatabasePage";
import PipelinePage from "./pages/PipelinePage";
import EditorPage from "./pages/EditorPage";
import EditorProjectPage from "./pages/EditorProjectPage";
import SettingsModal from "./components/SettingsModal";

type Theme = "light" | "dark";

// Two applications share this frontend and backend: the index console (building and
// searching the scene index) and video gen (turning a narration into a video).
type AppName = "index" | "video";

const APPS: Record<AppName, { name: string; description: string; home: string }> = {
  index: { name: "Index console", description: "Index episodes and search scenes", home: "/" },
  video: { name: "Video gen", description: "Turn a narration into a video", home: "/editor" },
};

// Which application a page belongs to; null for pages both share (Logs).
function appForPath(pathname: string): AppName | null {
  if (pathname.startsWith("/editor")) return "video";
  if (pathname.startsWith("/logs")) return null;
  return "index";
}

function getStoredApp(): AppName {
  try {
    return localStorage.getItem("app") === "video" ? "video" : "index";
  } catch {
    return "index";
  }
}

function getInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem("theme");
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // ignore - private browsing / storage blocked
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function App() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const [app, setApp] = useState<AppName>(() => appForPath(window.location.pathname) ?? getStoredApp());

  // following a link into the other application (or opening its URL) switches the menu with it
  const pathApp = appForPath(location.pathname);
  if (pathApp && pathApp !== app) setApp(pathApp);

  useEffect(() => {
    try {
      localStorage.setItem("app", app);
    } catch {
      // ignore
    }
  }, [app]);

  // opening the bare address goes back to whichever application was used last
  useEffect(() => {
    if (window.location.pathname === "/" && getStoredApp() === "video") navigate("/editor", { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [launcherOpen, setLauncherOpen] = useState(false);
  const launcherRef = useRef<HTMLDivElement>(null);

  // close the application menu on a click anywhere else, or Escape
  useEffect(() => {
    if (!launcherOpen) return;
    const onPointer = (e: MouseEvent) => {
      if (!launcherRef.current?.contains(e.target as Node)) setLauncherOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLauncherOpen(false);
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [launcherOpen]);

  function switchApp(next: AppName) {
    setLauncherOpen(false);
    setApp(next);
    navigate(APPS[next].home);
  }

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("theme", theme);
    } catch {
      // ignore
    }
  }, [theme]);

  return (
    <div className="app">
      <nav className="topnav">
        <span className="brand">{APPS[app].name}</span>
        {app === "index" ? (
          <>
            <NavLink to="/" end>
              Library
            </NavLink>
            <NavLink to="/scenes">Scenes</NavLink>
            <NavLink to="/search">Search</NavLink>
            <NavLink to="/pipeline">Pipeline</NavLink>
            <NavLink to="/logs">Logs</NavLink>
            <NavLink to="/database">Database</NavLink>
          </>
        ) : (
          <>
            <NavLink to="/editor">Videos</NavLink>
            <NavLink to="/logs">Logs</NavLink>
          </>
        )}
        <div className="topnav-spacer" />
        <ClaudeUsage />
        <div className="app-launcher" ref={launcherRef}>
          <button
            className="theme-toggle"
            onClick={() => setLauncherOpen((open) => !open)}
            aria-label="Switch application"
            aria-expanded={launcherOpen}
            title="Switch application"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
              {[2, 8, 14].flatMap((y) => [2, 8, 14].map((x) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.6" />))}
            </svg>
          </button>
          {launcherOpen && (
            <div className="app-launcher-menu" role="menu">
              {(Object.keys(APPS) as AppName[]).map((key) => (
                <button key={key} role="menuitem" className={key === app ? "active" : ""} onClick={() => switchApp(key)}>
                  <strong>{APPS[key].name}</strong>
                  <span>{APPS[key].description}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <button className="theme-toggle" onClick={() => setSettingsOpen(true)} aria-label="Settings" title="Settings">
          ⚙
        </button>
        <button
          className="theme-toggle"
          onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          aria-label="Toggle theme"
          title="Toggle theme"
        >
          {theme === "dark" ? "☀" : "☾"}
        </button>
      </nav>
      <main className="content">
        <Routes>
          <Route path="/" element={<LibraryPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/scenes" element={<ScenesPage />} />
          <Route path="/scenes/:id" element={<SceneDetailPage />} />
          <Route path="/sources/:id" element={<SourceDetailPage />} />
          <Route path="/database" element={<DatabasePage />} />
          <Route path="/pipeline" element={<PipelinePage />} />
          <Route path="/logs" element={<LogsPage />} />
          <Route path="/editor" element={<EditorPage />} />
          <Route path="/editor/:id" element={<EditorProjectPage />} />
        </Routes>
      </main>
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}

export default App;
