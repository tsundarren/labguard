import { useEffect, useState } from "react";
import { fmtDate, getMessage } from "../api";
import type { MessageDetail, PatientRow } from "../api";

interface Props {
  id: string;
  refreshKey: number;
}

const IDENTITY_FIELDS: [keyof PatientRow, string][] = [
  ["mrn", "MRN"],
  ["family_name", "Family name"],
  ["given_name", "Given name"],
  ["birth_date", "Date of birth"],
];

export default function MessageDetailView({ id, refreshKey }: Props) {
  const [detail, setDetail] = useState<MessageDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMessage(id)
      .then((d) => {
        if (!cancelled) {
          setDetail(d);
          setError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [id, refreshKey]);

  if (error) return <p className="error">Could not load message: {error}. <a href="#/">Back</a></p>;
  if (!detail) return <p className="muted">Loading…</p>;

  const orderPatient = detail.patients.find((p) => p.role === "order");
  const resultPatient = detail.patients.find((p) => p.role === "result");

  return (
    <section>
      <p><a href="#/">← Back to findings</a></p>
      <h2>Message {detail.message.id.slice(0, 8)}</h2>
      <p className="muted">
        Status <b>{detail.message.status}</b> · attempts {detail.message.attempts} · received {fmtDate(detail.message.received_at)} · key {detail.message.idempotency_key}
      </p>

      {detail.findings.length > 0 && (
        <>
          <h3>Findings</h3>
          <ul>
            {detail.findings.map((f) => (
              <li key={f.id}>
                <span className={`badge ${f.severity}`}>{f.severity}</span> <b>{f.rule_id}</b> ({f.field}): {f.description}{" "}
                <span className={`badge status-${f.status}`}>{f.status.replace("_", " ")}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <h3>Requisition vs. lab record</h3>
      {orderPatient && resultPatient ? (
        <table>
          <thead><tr><th>Field</th><th>Requisition</th><th>Lab record</th></tr></thead>
          <tbody>
            {IDENTITY_FIELDS.map(([key, label]) => {
              const a = orderPatient[key] ?? "—";
              const b = resultPatient[key] ?? "—";
              return (
                <tr key={key} className={a !== b ? "differs" : ""}>
                  <td>{label}</td><td>{a}</td><td>{b}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <p className="muted">Not processed yet.</p>
      )}

      {detail.order && detail.result && (
        <>
          <h3>Order and result</h3>
          <table>
            <tbody>
              <tr><td>Ordered test</td><td>{detail.order.test_code} {detail.order.test_display}</td></tr>
              <tr><td>Performed test</td><td>{detail.result.test_code} {detail.result.test_display}</td></tr>
              <tr><td>Ordering provider</td><td>{detail.order.requester ?? "—"}</td></tr>
              <tr><td>Specimen</td><td>{detail.result.specimen_type ?? "—"} · {detail.result.specimen_identifier ?? "no ID"}</td></tr>
              <tr><td>Collected</td><td>{fmtDate(detail.result.collected_at)}</td></tr>
              <tr><td>Result issued</td><td>{fmtDate(detail.result.issued)}</td></tr>
              <tr><td>Value</td><td>{detail.result.value ?? "—"} {detail.result.unit ?? ""}</td></tr>
            </tbody>
          </table>
        </>
      )}

      <h3>Audit trail</h3>
      <table>
        <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Details</th></tr></thead>
        <tbody>
          {detail.audit.map((a) => (
            <tr key={a.id}>
              <td>{fmtDate(a.occurred_at)}</td><td>{a.actor}</td><td>{a.action}</td>
              <td><code>{JSON.stringify(a.details)}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
