import { useEffect, useState } from "react";
import { fmtDate, getFindings, updateFinding } from "../api";
import type { FindingList, FindingStatus } from "../api";

interface Props {
  rules: string[];
  refreshKey: number;
  onChanged: () => void;
}

const PAGE = 25;

export default function FindingsTable({ rules, refreshKey, onChanged }: Props) {
  const [status, setStatus] = useState("open");
  const [rule, setRule] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<FindingList | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false; // ignore responses from a stale request
    getFindings({ status: status || undefined, rule_id: rule || undefined, limit: PAGE, offset })
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [status, rule, offset, refreshKey]);

  async function change(id: number, next: FindingStatus) {
    try {
      await updateFinding(id, next);
      onChanged();
    } catch (e) {
      setError(String(e));
    }
  }

  const total = data?.total ?? 0;

  return (
    <section>
      <h2>Findings</h2>
      <div className="filters">
        <label>
          Status{" "}
          <select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
            <option value="open">Open</option>
            <option value="resolved">Resolved</option>
            <option value="false_positive">False positive</option>
            <option value="">All</option>
          </select>
        </label>
        <label>
          Rule{" "}
          <select value={rule} onChange={(e) => { setRule(e.target.value); setOffset(0); }}>
            <option value="">All</option>
            {rules.map((r) => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </label>
        <span className="muted">{total} matching</span>
      </div>

      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Rule</th><th>Severity</th><th>Field</th><th>Description</th><th>Message</th><th>Created</th><th>Status</th><th></th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((f) => (
              <tr key={f.id}>
                <td>{f.rule_id}</td>
                <td><span className={`badge ${f.severity}`}>{f.severity}</span></td>
                <td>{f.field}</td>
                <td>{f.description}</td>
                <td><a href={`#/messages/${f.message_id}`}>{f.message_id.slice(0, 8)}</a></td>
                <td>{fmtDate(f.created_at)}</td>
                <td><span className={`badge status-${f.status}`}>{f.status.replace("_", " ")}</span></td>
                <td className="actions">
                  {f.status !== "resolved" && <button onClick={() => change(f.id, "resolved")}>Resolve</button>}
                  {f.status !== "false_positive" && <button onClick={() => change(f.id, "false_positive")}>False positive</button>}
                  {f.status !== "open" && <button onClick={() => change(f.id, "open")}>Reopen</button>}
                </td>
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr><td colSpan={8} className="muted">Nothing here.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="pager">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
        <span className="muted">
          {total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE, total)} of {total}
        </span>
        <button disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
      </div>
    </section>
  );
}
