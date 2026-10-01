import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import "./App.css";
import LibraryPage from "./pages/LibraryPage";
import SearchPage from "./pages/SearchPage";
import SceneDetailPage from "./pages/SceneDetailPage";
import ScenesPage from "./pages/ScenesPage";
import SourceDetailPage from "./pages/SourceDetailPage";
import DatabasePage from "./pages/DatabasePage";
import PipelinePage from "./pages/PipelinePage";
import TranscriptChunkSearchPage from "./pages/TranscriptChunkSearchPage";
import SettingsModal from "./components/SettingsModal";

type Theme = "light" | "dark";

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
        <span className="brand">Avatar Scene Indexer</span>
        <NavLink to="/" end>
          Library
        </NavLink>
        <NavLink to="/scenes">Scenes</NavLink>
        <NavLink to="/search">Search</NavLink>
        <NavLink to="/transcript-chunks">Chunks</NavLink>
        <NavLink to="/pipeline">Pipeline</NavLink>
        <NavLink to="/database">Database</NavLink>
        <div className="topnav-spacer" />
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
          <Route path="/transcript-chunks" element={<TranscriptChunkSearchPage />} />
        </Routes>
      </main>
      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}

export default App;
