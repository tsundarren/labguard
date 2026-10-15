import type { Stats } from "../api";

interface Props {
  stats: Stats | null;
  error: string | null;
}

export default function StatsPanel({ stats, error }: Props) {
  if (error) return <p className="error">Could not load stats: {error}</p>;
  if (!stats) return <p className="muted">Loading…</p>;

  const { messages, rules } = stats;
  const openTotal = rules.reduce((sum, r) => sum + r.open, 0);
  const maxTotal = Math.max(1, ...rules.map((r) => r.total));

  return (
    <section>
      <div className="cards">
        <Card label="Messages" value={messages.total ?? 0} />
        <Card label="Processed" value={messages.done ?? 0} />
        <Card label="Pending" value={messages.pending ?? 0} />
        <Card label="Failed" value={messages.failed ?? 0} />
        <Card label="Open findings" value={openTotal} />
      </div>
      <h2>Findings by rule</h2>
      {rules.length === 0 && <p className="muted">No findings yet. Seed some data and let the worker run.</p>}
      {rules.map((r) => (
        <div className="bar-row" key={r.rule_id}>
          <span className="bar-label">{r.rule_id}</span>
          <div className="bar-track">
            <div className={`bar-fill ${r.severity}`} style={{ width: `${(r.total / maxTotal) * 100}%` }} />
          </div>
          <span className="bar-value">
            {r.total} ({messages.done ? ((r.total / messages.done) * 100).toFixed(1) : "0.0"}% of messages)
          </span>
        </div>
      ))}
    </section>
  );
}

function Card({ label, value }: { label: string; value: number }) {
  return (
    <div className="card">
      <div className="card-value">{value}</div>
      <div className="muted">{label}</div>
    </div>
  );
}
