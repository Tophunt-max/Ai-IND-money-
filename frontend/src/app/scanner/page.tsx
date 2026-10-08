"use client";

import { ArrowRight, Radar, Play, Activity } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, Empty, ErrorBox, Field, KV, Notice, PageTitle, Progress, Select } from "@/components/ui";
import { when } from "@/lib/api";
import { useApi, useJob } from "@/lib/hooks";

interface Candidate {
  symbol: string;
  reason: string;
  urgency?: string;
  metrics?: Record<string, unknown>;
  timestamp?: string;
}

interface ScannerStatus {
  enabled: boolean;
  running: boolean;
  cycle_count?: number;
  last_cycle_at?: string | null;
  watchlist_size?: number;
  cycle_seconds?: number;
  queue_size?: number;
  screeners?: unknown;
  last_candidates: Candidate[];
}

function CandidateList({ items }: { items: Candidate[] }) {
  return (
    <div className="-mx-2 divide-y divide-white/[0.04]">
      {items.map((c, i) => (
        <div key={`${c.symbol}-${i}`} className="flex items-start justify-between gap-3 rounded-xl px-2 py-3 hover:bg-white/[0.02]">
          <div className="flex min-w-0 gap-3">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-ink-700 to-ink-800 text-xs font-bold text-gray-200 ring-1 ring-white/[0.06]">
              {c.symbol.slice(0, 2)}
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium text-white">{c.symbol}</span>
                {c.urgency && <Badge tone={c.urgency === "high" ? "error" : c.urgency === "medium" ? "warning" : "neutral"}>{c.urgency}</Badge>}
              </div>
              <p className="mt-1 text-xs leading-relaxed text-gray-400">{c.reason}</p>
            </div>
          </div>
          <Link href={`/analyze?symbol=${encodeURIComponent(c.symbol)}`} className="link flex shrink-0 items-center gap-1 text-xs font-medium">
            Analyze <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>
      ))}
    </div>
  );
}

export default function ScannerPage() {
  const [max, setMax] = useState(5);
  const { isAdmin } = useAuth();
  const { job, error, running, start } = useJob();
  const bg = useApi<ScannerStatus>("/api/dashboard/scanner/status", 30000);
  const candidates: Candidate[] = job?.status === "done" ? job.result?.candidates || [] : [];

  return (
    <div className="space-y-6">
      <PageTitle title="Market scanner" icon={Radar} subtitle="AI screens the NIFTY 50 watchlist for trade candidates. Nothing is bought." />

      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Run a scan" icon={Play} className="lg:col-span-2">
          <p className="mb-4 text-sm text-gray-400">
            Needs live quotes, so it finds nothing until an INDstocks token is set (Broker page).
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Candidates" className="w-36">
              <Select value={max} onChange={(e) => setMax(Number(e.target.value))}>
                {[3, 5, 10, 20].map((n) => <option key={n} value={n}>{n}</option>)}
              </Select>
            </Field>
            <Button icon={Radar} loading={running} onClick={() => start({ kind: "scan", max_candidates: max })} disabled={!isAdmin}>
              {running ? "Scanning..." : "Run scan"}
            </Button>
          </div>
          {running && <div className="mt-4"><Progress value={60} /><p className="mt-2 text-xs text-gray-500">Scanning the market (up to a minute)...</p></div>}
          {!isAdmin && <p className="mt-3 text-xs text-gray-500">View-only account: running a scan needs an admin.</p>}
        </Card>

        <Card title="Background scanner" icon={Activity}
          right={bg.data && <Badge tone={bg.data.running ? "ok" : "neutral"} dot>{bg.data.running ? "Running" : bg.data.enabled ? "Stopped" : "Off"}</Badge>}>
          <ErrorBox error={bg.error} />
          {bg.data && (bg.data.running ? (
            <KV items={[
              ["Cycles", bg.data.cycle_count ?? 0],
              ["Every", `${bg.data.cycle_seconds ?? "—"}s`],
              ["Watchlist", bg.data.watchlist_size ?? "—"],
              ["Last cycle", when(bg.data.last_cycle_at || null)],
            ]} />
          ) : (
            <p className="text-sm text-gray-500">
              {bg.data.enabled ? "Enabled but not running in the API process." : "Set SKOPAQ_SCANNER_ENABLED=true (Environment page, then restart the API) to scan every cycle in the background."}
            </p>
          ))}
        </Card>
      </div>

      <ErrorBox error={error || (job?.status === "failed" ? job.error : null)} />

      {job?.status === "done" && (
        <Card title={`${candidates.length} candidates`} subtitle="From your scan">
          {candidates.length === 0
            ? <Empty icon={Radar}>No candidates this time (without an INDstocks token there are no quotes to screen).</Empty>
            : <CandidateList items={candidates} />}
        </Card>
      )}

      {bg.data && bg.data.last_candidates.length > 0 && (
        <Card title="Latest from the background scanner">
          <CandidateList items={bg.data.last_candidates} />
        </Card>
      )}

      {!job && !(bg.data?.last_candidates.length) && (
        <Notice tone="neutral" icon={Radar}>Run a scan to see candidates here.</Notice>
      )}
    </div>
  );
}
