"use client";

import { useState } from "react";

import { Badge, Button, Card, Empty, ErrorBox, Loading, PageTitle } from "@/components/ui";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { rcLabel, type SchedulerStatus } from "@/lib/scheduler";

export default function SchedulerPage() {
  const { data, error, loading, reload } = useApi<SchedulerStatus>("/api/dashboard/scheduler", 60000);
  const [log, setLog] = useState<{ day: string; job: string; lines: string[] } | null>(null);
  const [logErr, setLogErr] = useState<string | null>(null);

  const openLog = async (day: string, job: string) => {
    setLogErr(null);
    try {
      setLog(await api(`/api/dashboard/scheduler/log?day=${day}&job=${job}&lines=400`));
    } catch (e: any) {
      setLogErr(e.message);
    }
  };

  return (
    <div className="space-y-5">
      <PageTitle title="Scheduler" right={<Button variant="ghost" onClick={reload}>Refresh</Button>} />
      <ErrorBox error={error || (data && !data.ok ? data.error : null)} />
      {loading && !data ? (
        <Loading />
      ) : data?.ok ? (
        <>
          <Card
            title="Today's plan"
            right={
              <Badge tone={!data.enabled ? "error" : data.mode === "live" ? "error" : "warning"}>
                {data.enabled ? (data.mode || "").toUpperCase() : "DISABLED"}
              </Badge>
            }
          >
            <pre className="text-xs text-gray-300 whitespace-pre-wrap font-mono leading-relaxed">
              {data.lines.join("\n")}
            </pre>
          </Card>

          <Card title="Recent sessions">
            {data.days.length === 0 ? (
              <Empty>No sessions recorded in the last 10 days.</Empty>
            ) : (
              <div className="divide-y divide-gray-800">
                {data.days.map((d) => (
                  <div key={d.date} className="py-3 space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-sm">{d.date}</span>
                      {d.has_log && (
                        <div className="flex gap-3">
                          <button className="text-xs text-blue-400" onClick={() => openLog(d.date, "daemon")}>
                            Session log
                          </button>
                          {d.jobs.settle && (
                            <button className="text-xs text-blue-400" onClick={() => openLog(d.date, "settle")}>
                              Settle log
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                    {Object.entries(d.jobs).map(([job, j]) => {
                      const l = rcLabel(j);
                      return (
                        <div key={job} className="flex items-center gap-2 text-xs text-gray-400">
                          <span className="w-14 capitalize">{job}</span>
                          <Badge tone={l.tone}>{l.text}</Badge>
                        </div>
                      );
                    })}
                  </div>
                ))}
              </div>
            )}
          </Card>

          <ErrorBox error={logErr} />
          {log && (
            <Card
              title={`${log.job} log · ${log.day}`}
              right={<button className="text-xs text-gray-400" onClick={() => setLog(null)}>Close</button>}
            >
              <pre className="text-[11px] text-gray-300 whitespace-pre-wrap font-mono max-h-[60vh] overflow-y-auto">
                {log.lines.join("\n") || "(empty)"}
              </pre>
            </Card>
          )}
        </>
      ) : null}
    </div>
  );
}
