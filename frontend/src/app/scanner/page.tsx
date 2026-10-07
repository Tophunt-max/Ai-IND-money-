"use client";

import Link from "next/link";
import { useState } from "react";

import { Badge, Button, Card, Empty, ErrorBox, PageTitle } from "@/components/ui";
import { useJob } from "@/lib/hooks";

interface Candidate {
  symbol: string;
  reason: string;
  urgency?: string;
  metrics?: Record<string, unknown>;
  timestamp?: string;
}

export default function ScannerPage() {
  const [max, setMax] = useState(5);
  const { job, error, running, start } = useJob();
  const candidates: Candidate[] = job?.status === "done" ? job.result?.candidates || [] : [];

  return (
    <div className="space-y-5">
      <PageTitle title="Market scanner" />
      <Card>
        <p className="text-sm text-gray-400 mb-3">
          Screens the NIFTY 50 watchlist with the AI models and lists trade candidates. Nothing is
          bought. Needs live quotes, so it finds nothing until an INDstocks token is set.
        </p>
        <div className="flex items-center gap-3 flex-wrap">
          <label className="text-sm text-gray-400">
            Candidates
            <select
              value={max}
              onChange={(e) => setMax(Number(e.target.value))}
              className="ml-2 bg-gray-900 border border-gray-700 rounded px-2 py-1"
            >
              {[3, 5, 10, 20].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <Button onClick={() => start({ kind: "scan", max_candidates: max })} disabled={running}>
            {running ? "Scanning..." : "Run scan"}
          </Button>
        </div>
      </Card>

      <ErrorBox error={error || (job?.status === "failed" ? job.error : null)} />
      {running && <div className="animate-pulse text-gray-400 text-sm">Scanning the market (up to a minute)...</div>}

      {job?.status === "done" && (
        <Card title={`${candidates.length} candidates`}>
          {candidates.length === 0 ? (
            <Empty>No candidates this time (without an INDstocks token there are no quotes to screen).</Empty>
          ) : (
            <div className="divide-y divide-gray-800">
              {candidates.map((c) => (
                <div key={c.symbol} className="py-3 flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{c.symbol}</span>
                      {c.urgency && <Badge tone={c.urgency === "high" ? "error" : "warning"}>{c.urgency}</Badge>}
                    </div>
                    <p className="text-xs text-gray-400 mt-1">{c.reason}</p>
                  </div>
                  <Link
                    href={`/analyze?symbol=${encodeURIComponent(c.symbol)}`}
                    className="text-xs text-blue-400 shrink-0"
                  >
                    Analyze →
                  </Link>
                </div>
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
