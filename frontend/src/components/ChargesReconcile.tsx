"use client";

import { Receipt } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, Empty, ErrorBox, KV, Notice, Table } from "@/components/ui";
import { api, ApiError, inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Report {
  saved_at?: string; by?: string; broker_total: number; estimated_total: number; difference: number;
  discrepancies: string[]; unmatched_broker: string[]; unmatched_estimates: string[];
  groups: { date: string; symbol: string; broker: number | null; estimated: number | null; difference?: number }[];
}

/** Import the broker's contract note (CSV) and compare its charges with Skopaq's estimates. */
export default function ChargesReconcile() {
  const { isAdmin } = useAuth();
  const saved = useApi<{ reports: Report[] }>("/api/dashboard/finance/reconciliation", 120000);
  const [csv, setCsv] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<Report | null>(null);

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    if (f.size > 2_000_000) { setErr("File too large (2 MB max)"); return; }
    setCsv(await f.text());
  };
  const send = async () => {
    setBusy(true);
    setErr(null);
    try {
      setRes(await api<Report>("/api/dashboard/finance/contract-note", { method: "POST", json: { csv, days: 7 } }));
      saved.reload();
    } catch (e: any) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const r = res || saved.data?.reports?.[0] || null;
  return (
    <Card title="Charges reconciliation" icon={Receipt}
      subtitle="Net P&L stays 'estimated' until the broker's contract note confirms the charges">
      <ErrorBox error={err || saved.error} />
      {isAdmin && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <input type="file" accept=".csv,text/csv" onChange={(e) => onFile(e.target.files?.[0])}
            className="text-xs text-gray-400 file:mr-2 file:rounded-lg file:border-0 file:bg-white/6 file:px-3 file:py-1.5 file:text-gray-200" />
          <Button size="sm" loading={busy} disabled={csv.length < 10} onClick={send}>Reconcile last 7 days</Button>
        </div>
      )}
      {!r ? <Empty>No contract note imported yet.</Empty> : (
        <div className="space-y-3">
          <KV cols={3} items={[
            ["Broker charges", inr(r.broker_total)], ["Estimated", inr(r.estimated_total)],
            ["Difference", <span key="d" className={Math.abs(r.difference) > 1 ? "text-amber-300" : "text-emerald-300"}>{inr(r.difference)}</span>],
          ]} />
          {r.saved_at && <div className="text-[11px] text-gray-500">Saved {when(r.saved_at)} by {r.by}</div>}
          {r.discrepancies.length > 0 ? <Notice tone="warning" title={`${r.discrepancies.length} discrepancy(ies)`}>{r.discrepancies.slice(0, 5).join(" · ")}</Notice>
            : <Badge tone="ok">charges match within ₹1</Badge>}
          <Table head={["Day / symbol", "Broker", "Estimate", "Diff"]}>
            {r.groups.slice(0, 30).map((g) => (
              <tr key={`${g.date}${g.symbol}`}><td>{g.date} {g.symbol}</td><td>{inr(g.broker)}</td><td>{inr(g.estimated)}</td><td>{g.difference != null ? inr(g.difference) : "—"}</td></tr>
            ))}
          </Table>
        </div>
      )}
    </Card>
  );
}
