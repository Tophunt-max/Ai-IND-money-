"use client";

import type { LucideIcon } from "lucide-react";
import { Brain, CheckCheck, Dices, FlaskConical, ListChecks, NotebookPen, Radar, RefreshCw } from "lucide-react";
import Link from "next/link";

import { Badge, Button, Card, Empty, ErrorBox, PageTitle, Skeleton } from "@/components/ui";
import { when } from "@/lib/api";
import { useApi, type Job } from "@/lib/hooks";

const KINDS: Record<Job["kind"], { label: string; icon: LucideIcon; href: (j: Job) => string }> = {
  analyze: { label: "Analysis", icon: Brain, href: (j) => `/analyze?symbol=${j.symbol}` },
  trade: { label: "Analysis + paper trade", icon: NotebookPen, href: (j) => `/analyze?symbol=${j.symbol}&trade=1` },
  scan: { label: "Market scan", icon: Radar, href: () => "/scanner" },
  backtest: { label: "Backtest", icon: FlaskConical, href: () => "/backtest" },
  montecarlo: { label: "Monte Carlo", icon: Dices, href: () => "/backtest" },
  settle: { label: "Settle decisions", icon: CheckCheck, href: () => "/scheduler" },
};

const TONE = { queued: "neutral", running: "info", done: "ok", failed: "error" } as const;

function duration(j: Job): string {
  if (!j.started_at) return "—";
  const s = Math.round((j.finished_at || Date.now() / 1000) - j.started_at);
  return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
}

export default function JobsPage() {
  const { data, error, loading, reload } = useApi<{ jobs: Job[] }>("/api/dashboard/jobs", 5000);
  const jobs = data?.jobs || [];

  return (
    <div className="space-y-6">
      <PageTitle title="Jobs" icon={ListChecks} subtitle="Analyses, scans, backtests and settles started from the dashboard (one at a time)"
        right={<Button variant="ghost" icon={RefreshCw} onClick={reload}>Refresh</Button>} />
      <ErrorBox error={error} />
      <Card padded={false}>
        {loading && !data ? (
          <div className="space-y-3 p-5"><Skeleton className="h-12" /><Skeleton className="h-12" /></div>
        ) : jobs.length === 0 ? (
          <Empty icon={ListChecks} title="No jobs yet">Jobs are kept in memory and cleared when the API restarts.</Empty>
        ) : (
          <div className="divide-y divide-white/[0.04]">
            {jobs.map((j) => {
              const k = KINDS[j.kind] || KINDS.analyze;
              const Icon = k.icon;
              return (
                <Link key={j.id} href={k.href(j)} className="flex items-center gap-4 px-5 py-4 transition hover:bg-white/[0.02]">
                  <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-white/[0.04] ring-1 ring-white/[0.06]">
                    <Icon className="h-5 w-5 text-brand-300" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-white">{k.label}</span>
                      {j.symbol && <span className="font-mono text-xs text-gray-400">{j.symbol}</span>}
                    </div>
                    <div className="truncate text-xs text-gray-500">
                      {when(new Date(j.created_at * 1000).toISOString())} · {duration(j)}{j.by && ` · ${j.by}`}
                      {j.error && <span className="text-rose-300"> · {j.error}</span>}
                    </div>
                  </div>
                  <Badge tone={TONE[j.status]} dot>{j.status}</Badge>
                </Link>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
