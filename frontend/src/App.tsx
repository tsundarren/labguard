import { useCallback, useEffect, useState } from "react";
import { getConfig, getStats } from "./api";
import type { Stats } from "./api";
import FindingsTable from "./components/FindingsTable";
import MessageDetailView from "./components/MessageDetail";
import StatsPanel from "./components/StatsPanel";

function useHash(): string {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export default function App() {
  const hash = useHash();
  const [stats, setStats] = useState<Stats | null>(null);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [readOnly, setReadOnly] = useState(false);

  useEffect(() => {
    getConfig()
      .then((c) => setReadOnly(c.read_only))
      .catch(() => setReadOnly(false));
  }, []);

  useEffect(() => {
    getStats()
      .then((s) => {
        setStats(s);
        setStatsError(null);
      })
      .catch((e) => setStatsError(String(e)));
  }, [refreshKey]);

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);
  const messageMatch = hash.match(/^#\/messages\/([0-9a-fA-F-]{36})$/);

  return (
    <div className="page">
      <header>
        <a href="#/" className="brand">
          LabGuard
        </a>
        <span className="muted">synthetic data only</span>
        <button onClick={refresh}>Refresh</button>
      </header>
      {readOnly && <p className="banner">Read-only demo with synthetic data. Finding statuses can't be changed here.</p>}
      {messageMatch ? (
        <MessageDetailView id={messageMatch[1]} refreshKey={refreshKey} />
      ) : (
        <>
          <StatsPanel stats={stats} error={statsError} />
          <FindingsTable rules={stats?.rules.map((r) => r.rule_id) ?? []} refreshKey={refreshKey} onChanged={refresh} readOnly={readOnly} />
        </>
      )}
    </div>
  );
}
