"use client";

import { CalendarClock, CheckCheck, FileText, RefreshCw, X } from "lucide-react";
import { useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, Empty, ErrorBox, Notice, PageTitle, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi, useJob } from "@/lib/hooks";
import { rcLabel, type SchedulerStatus } from "@/lib/scheduler";

export default function SchedulerPage() {
  const { isAdmin } = useAuth();
  const { data, error, loading, reload } = useApi<SchedulerStatus>("/api/dashboard/scheduler", 60000);
  const [log, setLog] = useState<{ day: string; job: string; lines: string[] } | null>(null);
  const [logErr, setLogErr] = useState<string | null>(null);
  const settle = useJob();

  const openLog = async (day: string, job: string) => {
    setLogErr(null);
    try {
      setLog(await api(`/api/dashboard/scheduler/log?day=${day}&job=${job}&lines=400`));
    } catch (e: any) {
      setLogErr(e.message);
    }
  };

  const runSettle = () => {
    if (!confirm("Settle past AI decisions now? It scores their outcome and writes reflections (LLM calls). No orders are placed.")) return;
    settle.start({ kind: "settle" });
  };

  return (
    <div className="space-y-6">
      <PageTitle title="Scheduler" icon={CalendarClock} subtitle="One autonomous session per NSE trading day"
        right={<>
          {isAdmin && <Button variant="ghost" icon={CheckCheck} loading={settle.running} onClick={runSettle}>Settle now</Button>}
          <Button variant="ghost" icon={RefreshCw} onClick={reload}>Refresh</Button>
        </>} />

      <ErrorBox error={error || (data && !data.ok ? data.error : null) || settle.error || (settle.job?.status === "failed" ? settle.job.error : null)} />
      {settle.job?.status === "done" && <Notice tone="ok">Settled {settle.job.result?.settled ?? 0} decisions.</Notice>}
      {settle.running && <Notice tone="info">Settling past decisions (LLM reflections, can take a few minutes)...</Notice>}

      {loading && !data ? <Skeleton className="h-40" /> : data?.ok ? (
        <>
          <div className="grid gap-5 lg:grid-cols-5">
            <Card title="Today's plan" className="lg:col-span-3"
              right={<Badge tone={!data.enabled ? "neutral" : data.mode === "live" ? "error" : "warning"} dot>
                {data.enabled ? (data.mode || "").toUpperCase() : "DISABLED"}</Badge>}>
              <div className="space-y-2">
                {data.lines.map((line, i) => {
                  const at = line.indexOf(":");
                  return at > 0 && at < 24 ? (
                    <div key={i} className="flex gap-4 text-sm">
                      <span className="w-32 shrink-0 text-gray-500">{line.slice(0, at)}</span>
                      <span className="min-w-0 break-words text-gray-200">{line.slice(at + 1).trim()}</span>
                    </div>
                  ) : <div key={i} className="text-sm text-gray-400">{line}</div>;
                })}
              </div>
            </Card>

            <Card title="Recent sessions" className="lg:col-span-2">
              {data.days.length === 0 ? <Empty>No sessions in the last 10 days.</Empty> : (
                <ol className="relative space-y-4 border-l border-white/[0.08] pl-5">
                  {data.days.map((d) => (
                    <li key={d.date} className="relative">
                      <span className="absolute -left-[25px] top-1.5 h-2.5 w-2.5 rounded-full bg-brand-400 ring-4 ring-ink-900" />
                      <div className="flex items-center justify-between gap-2">
                        <span className="num text-sm font-medium text-white">{d.date}</span>
                        {d.has_log && (
                          <div className="flex gap-2">
                            <button className="link flex items-center gap-1 text-xs" onClick={() => openLog(d.date, "daemon")}>
                              <FileText className="h-3.5 w-3.5" /> Log
                            </button>
                            {d.jobs.settle && <button className="link text-xs" onClick={() => openLog(d.date, "settle")}>Settle</button>}
                          </div>
                        )}
                      </div>
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {Object.entries(d.jobs).map(([job, j]) => {
                          const l = rcLabel(j);
                          return <Badge key={job} tone={l.tone}><span className="capitalize">{job}</span>: {l.text}</Badge>;
                        })}
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </Card>
          </div>

          <ErrorBox error={logErr} />
          {log && (
            <Card title={`${log.job} log · ${log.day}`}
              right={<button className="rounded-lg p-1.5 text-gray-400 hover:bg-white/[0.06]" onClick={() => setLog(null)}><X className="h-4 w-4" /></button>}>
              <pre className="max-h-[60vh] overflow-y-auto whitespace-pre-wrap rounded-xl bg-black/40 p-4 font-mono text-[11px] leading-relaxed text-gray-300 ring-1 ring-white/[0.05]">
                {log.lines.join("\n") || "(empty)"}
              </pre>
            </Card>
          )}
        </>
      ) : null}
    </div>
  );
}
