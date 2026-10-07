"use client";

import { useState } from "react";

import { Badge, Button, Card, Empty, ErrorBox, Loading, PageTitle } from "@/components/ui";
import { inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Trade {
  id: string | null;
  symbol: string;
  side: string;
  quantity: number;
  price: number | null;
  status: string;
  is_paper: boolean;
  pnl: number | null;
  confidence: number | null;
  reason: string;
  closed_at: string | null;
  created_at: string | null;
}

const MODES = ["current", "paper", "live", "all"] as const;

export default function TradesPage() {
  const [mode, setMode] = useState<(typeof MODES)[number]>("current");
  const [open, setOpen] = useState<string | null>(null);
  const { data, error, loading, reload } = useApi<{ trades: Trade[] }>(
    `/api/dashboard/trades?limit=200&mode=${mode}`,
  );
  const trades = data?.trades || [];

  return (
    <div className="space-y-5">
      <PageTitle title="Trades" right={<Button variant="ghost" onClick={reload}>Refresh</Button>} />

      <div className="flex gap-2 flex-wrap">
        {MODES.map((m) => (
          <button
            key={m}
            onClick={() => setMode(m)}
            className={`px-3 py-1 rounded-full text-xs border ${
              mode === m ? "border-blue-500 text-blue-300" : "border-gray-700 text-gray-400"
            }`}
          >
            {m === "current" ? "Current mode" : m}
          </button>
        ))}
      </div>

      <ErrorBox error={error} />
      <Card>
        {loading && !data ? (
          <Loading />
        ) : trades.length === 0 ? (
          <Empty>No trades yet.</Empty>
        ) : (
          <div className="divide-y divide-gray-800">
            {trades.map((t, i) => {
              const key = t.id || String(i);
              return (
                <div key={key} className="py-3">
                  <button className="w-full text-left" onClick={() => setOpen(open === key ? null : key)}>
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2">
                        <Badge tone={t.side === "BUY" ? "ok" : "error"}>{t.side}</Badge>
                        <span className="font-medium">{t.symbol}</span>
                        <span className="text-xs text-gray-500">
                          {t.quantity} @ {inr(t.price)}
                        </span>
                      </div>
                      <div className="text-right">
                        {t.pnl !== null && (
                          <div className={`text-sm font-semibold ${t.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                            {inr(t.pnl)}
                          </div>
                        )}
                        <div className="text-xs text-gray-500">{when(t.created_at)}</div>
                      </div>
                    </div>
                    <div className="flex gap-2 mt-1 text-xs text-gray-500">
                      <span>{t.status}</span>
                      <span>· {t.is_paper ? "paper" : "LIVE"}</span>
                      {t.confidence != null && <span>· {t.confidence}% confidence</span>}
                      {t.side === "BUY" && <span>· {t.closed_at ? `closed ${when(t.closed_at)}` : "open"}</span>}
                    </div>
                  </button>
                  {open === key && t.reason && (
                    <p className="text-xs text-gray-400 mt-2 whitespace-pre-wrap">{t.reason}</p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
