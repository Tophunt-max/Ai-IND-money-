import type { Tone } from "@/components/ui";

export interface SchedulerJob {
  started: string;
  rc: number | null;
}

export interface SchedulerStatus {
  ok: boolean;
  error?: string;
  enabled?: boolean;
  mode?: string;
  lines: string[];
  days: { date: string; has_log: boolean; jobs: Record<string, SchedulerJob> }[];
}

export function rcLabel(job: SchedulerJob): { text: string; tone: Tone } {
  if (job.started.startsWith("skipped")) return { text: job.started, tone: "warning" };
  if (job.rc === null) return { text: "running / no result yet", tone: "neutral" };
  if (job.rc === 0) return { text: "finished OK", tone: "ok" };
  return { text: `exit code ${job.rc}`, tone: "error" };
}

/** "Next session: ..." from the plan lines. */
export function planLine(lines: string[], key: string): string {
  const line = lines.find((l) => l.startsWith(key));
  return line ? line.slice(line.indexOf(":") + 1).trim() : "";
}
