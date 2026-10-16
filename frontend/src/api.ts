export type FindingStatus = "open" | "resolved" | "false_positive";

export interface Finding {
  id: number;
  message_id: string;
  rule_id: string;
  severity: "error" | "warning";
  field: string;
  description: string;
  status: FindingStatus;
  created_at: string;
}

export interface FindingList {
  total: number;
  items: Finding[];
}

export interface RuleStats {
  rule_id: string;
  severity: string;
  open: number;
  resolved: number;
  false_positive: number;
  total: number;
}

export interface Stats {
  messages: Record<string, number>;
  rules: RuleStats[];
}

export interface PatientRow {
  role: "order" | "result";
  mrn: string | null;
  family_name: string | null;
  given_name: string | null;
  birth_date: string | null;
}

export interface AuditEntry {
  id: number;
  occurred_at: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string;
  details: Record<string, unknown>;
}

export interface MessageDetail {
  message: { id: string; status: string; attempts: number; received_at: string; idempotency_key: string };
  patients: PatientRow[];
  order: { test_code: string | null; test_display: string | null; requester: string | null; authored_on: string | null } | null;
  result: {
    test_code: string | null;
    test_display: string | null;
    value: number | string | null;
    unit: string | null;
    issued: string | null;
    specimen_type: string | null;
    specimen_identifier: string | null;
    collected_at: string | null;
  } | null;
  findings: Finding[];
  audit: AuditEntry[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

export const getStats = () => request<Stats>("/api/stats");

export const getConfig = () => request<{ read_only: boolean }>("/api/config");

export function getFindings(p: { status?: string; rule_id?: string; limit: number; offset: number }) {
  const q = new URLSearchParams({ limit: String(p.limit), offset: String(p.offset) });
  if (p.status) q.set("status", p.status);
  if (p.rule_id) q.set("rule_id", p.rule_id);
  return request<FindingList>(`/api/findings?${q.toString()}`);
}

export const updateFinding = (id: number, status: FindingStatus) =>
  request<Finding>(`/api/findings/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });

export const getMessage = (id: string) => request<MessageDetail>(`/api/messages/${id}`);

export const fmtDate = (value: string | null | undefined) => (value ? new Date(value).toLocaleString() : "—");
