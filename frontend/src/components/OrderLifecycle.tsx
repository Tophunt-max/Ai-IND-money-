"use client";

import { GitCommitHorizontal } from "lucide-react";

import { Badge, Card, Empty, ErrorBox, Skeleton, Table, type Tone } from "@/components/ui";
import { when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Lc {
  key: string; order_id: string; symbol: string; side: string; qty: number; filled: number;
  state: string; terminal: boolean; history: { ts: string; state: string }[]; anomalies: string[];
}

const TONE: Record<string, Tone> = {
  FILLED: "ok", CANCELLED: "neutral", REJECTED: "error", EXPIRED: "neutral", FAILED: "error",
  UNKNOWN: "warning", RECONCILIATION_REQUIRED: "error", PARTIALLY_FILLED: "info",
  ACKNOWLEDGED: "info", SUBMITTED: "info", CANCEL_PENDING: "warning", CREATED: "neutral", VALIDATED: "neutral",
};

/** Today's live orders through their 13-state lifecycle, from the order journal. */
export default function OrderLifecycle() {
  const lc = useApi<{ orders: Lc[]; open: number; anomalies: number }>("/api/dashboard/control/lifecycle", 15000);
  return (
    <Card title="Order lifecycle" icon={GitCommitHorizontal}
      subtitle="Every live order today, step by step: created, validated, submitted, acknowledged, filled (or why not). Broker evidence decides each step."
      right={lc.data && <Badge tone={lc.data.open ? "warning" : "ok"}>{lc.data.open} not final</Badge>}>
      <ErrorBox error={lc.error} />
      {lc.loading && !lc.data ? <Skeleton className="h-16" /> : !lc.data?.orders.length ? (
        <Empty>No live orders today (paper orders fill inside the session).</Empty>
      ) : (
        <Table head={["Order", "State", "Filled", "Path"]}>
          {lc.data.orders.slice(0, 50).map((o) => (
            <tr key={o.key} className="align-top">
              <td>{o.side} {o.qty} {o.symbol}<div className="text-[11px] text-gray-500">{o.order_id || o.key.slice(0, 12)}</div></td>
              <td><Badge tone={TONE[o.state] ?? "neutral"}>{o.state}</Badge>
                {o.anomalies.length > 0 && <div className="mt-1 text-[11px] text-amber-300">{o.anomalies.join("; ")}</div>}</td>
              <td>{o.filled} / {o.qty}</td>
              <td className="text-[11px] text-gray-500">{o.history.map((h) => `${h.state}${h.ts ? ` ${when(h.ts).split(", ").pop()}` : ""}`).join(" › ")}</td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}
