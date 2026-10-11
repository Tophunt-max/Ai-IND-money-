"use client";

import { BrainCircuit, Radar } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { inr, when } from "@/lib/api";
import { useApi, type Job } from "@/lib/hooks";
import { useQuotes } from "@/lib/live";
import { SETUP_LABEL, type SwingIdeaDict } from "@/lib/types";

import { useAuth } from "./AuthGate";
import { useOrderTicket } from "./OrderTicket";
import { Badge, Button, Change, Empty, Skeleton, SymbolAvatar } from "./ui";

/** The latest finished swing scan's ideas (a dashboard job), newest first. */
export function useLatestIdeas() {
  const jobs = useApi<{ jobs: Job[] }>("/api/dashboard/jobs", 120000);
  const job = useMemo(() => (jobs.data?.jobs || []).filter((j) => j.kind === "scan" && j.status === "done")
    .sort((a, b) => (b.finished_at || 0) - (a.finished_at || 0))[0], [jobs.data]);
  return {
    ideas: (job?.result?.ideas || []) as SwingIdeaDict[],
    model: job?.result?.model as string | null | undefined,
    at: job?.finished_at ? new Date(job.finished_at * 1000).toISOString() : null,
    loading: jobs.loading && !jobs.data,
    error: jobs.error,
  };
}

export function IdeaCard({ idea }: { idea: SwingIdeaDict }) {
  const { isAdmin } = useAuth();
  const ticket = useOrderTicket();
  const { quotes } = useQuotes([idea.symbol]);
  const q = quotes[idea.symbol];
  const risk = idea.entry - idea.stop;
  const rr = risk > 0 ? (idea.target - idea.entry) / risk : null;
  return (
    <div className="surface flex w-[260px] shrink-0 flex-col gap-3 p-4 transition hover:-translate-y-0.5 hover:shadow-pop sm:w-auto">
      <Link href={`/market?symbol=${encodeURIComponent(idea.symbol)}`} className="flex items-center gap-3">
        <SymbolAvatar symbol={idea.symbol} size={40} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-bold text-white">{idea.symbol}</div>
          <Badge tone="info">{SETUP_LABEL[idea.setup] || idea.setup}</Badge>
        </div>
        {idea.prob != null && (
          <div className="text-right">
            <div className="num text-lg font-bold text-emerald-400">{Math.round(idea.prob * 100)}%</div>
            <div className="text-[10px] text-gray-500">P(win)</div>
          </div>
        )}
      </Link>
      <div className="num grid grid-cols-3 gap-1 rounded-xl bg-ink-850 p-2 text-center text-[11px]">
        <div><div className="text-gray-500">Entry</div><div className="font-semibold text-white">{inr(idea.entry, 0)}</div></div>
        <div><div className="text-gray-500">Stop</div><div className="font-semibold text-rose-400">{inr(idea.stop, 0)}</div></div>
        <div><div className="text-gray-500">Target</div><div className="font-semibold text-emerald-400">{inr(idea.target, 0)}</div></div>
      </div>
      <div className="flex items-center justify-between text-xs">
        <span className="num text-gray-500">Now {inr(q?.ltp)} <Change value={q?.change_pct} size="xs" pill={false} /></span>
        {rr != null && <span className="text-gray-500">R:R {rr.toFixed(1)}</span>}
      </div>
      {isAdmin && <Button size="sm" variant="soft" onClick={() => ticket({ symbol: idea.symbol, side: "BUY", stop: idea.stop, target: idea.target })}>Buy with these levels</Button>}
    </div>
  );
}

export function IdeasStrip({ limit = 6 }: { limit?: number }) {
  const { ideas, model, at, loading } = useLatestIdeas();
  return (
    <section>
      <div className="mb-3 flex items-end justify-between gap-2">
        <div>
          <h2 className="flex items-center gap-2 text-[15px] font-semibold text-white"><BrainCircuit className="h-4 w-4 text-brand-400" /> AI swing ideas</h2>
          <p className="text-xs text-gray-500">{at ? `From the scan of ${when(at)}${model ? ` · model ${model}` : ""}` : "Run a scan to see today's ideas"}</p>
        </div>
        <Link href="/ai" className="link text-sm font-semibold">See all</Link>
      </div>
      {loading ? <Skeleton className="h-40" /> : ideas.length === 0 ? (
        <div className="surface"><Empty icon={Radar} title="No ideas yet">
          The Adaptive AI scores today&apos;s daily setups when you run a swing scan. <Link href="/scanner" className="link">Run a scan</Link>
        </Empty></div>
      ) : (
        <div className="no-scrollbar -mx-3 flex gap-3 overflow-x-auto px-3 pb-1 sm:mx-0 sm:grid sm:grid-cols-2 sm:px-0 xl:grid-cols-3">
          {ideas.slice(0, limit).map((i) => <IdeaCard key={i.symbol} idea={i} />)}
        </div>
      )}
    </section>
  );
}
