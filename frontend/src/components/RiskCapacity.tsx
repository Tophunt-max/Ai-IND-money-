"use client";

import { ShieldAlert, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { Badge, Card, Progress, Skeleton } from "@/components/ui";
import { inr } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Risk {
  kill_switch: { halted: boolean };
  ceilings: Record<string, number | boolean>;
  working: Record<string, number>;
  realized: { day: number; loss_streak: number } | null;
  realized_source: string;
}

/** Remaining risk capacity today: loss streak, entry blockers, realized P&L vs the stop. */
export default function RiskCapacity({ portfolioValue }: { portfolioValue?: number | null }) {
  const risk = useApi<Risk>("/api/dashboard/risk", 30000);
  const rec = useApi<{ blocking_entries: boolean; blockers: unknown[] }>("/api/dashboard/control/reconcile", 30000);
  const r = risk.data;
  if (!r) return <Skeleton className="h-28" />;
  const stopPct = Number(r.ceilings.max_daily_loss_pct) || 0;
  const limit = portfolioValue ? portfolioValue * stopPct : null;
  const lost = Math.max(0, -(r.realized?.day ?? 0));
  const used = limit ? Math.min(100, (lost / limit) * 100) : 0;
  const streak = r.realized?.loss_streak ?? 0;
  const maxStreak = r.working.max_consecutive_losses;
  const blocked = r.kill_switch.halted || !!rec.data?.blocking_entries || streak >= maxStreak || used >= 100;
  return (
    <Card title="Risk capacity today" icon={blocked ? ShieldAlert : ShieldCheck}
      subtitle="What is left before the hard stops refuse new entries (exits always run)"
      right={<Link href="/signals"><Badge tone={blocked ? "error" : "ok"} dot>{blocked ? "Entries blocked" : "Entries allowed"}</Badge></Link>}>
      <div className="space-y-3 text-sm">
        <div>
          <div className="mb-1 flex justify-between text-xs text-gray-400">
            <span>Daily loss stop ({(stopPct * 100).toFixed(0)}%)</span>
            <span className="num">{inr(lost, 0)} lost{limit ? ` of ${inr(limit, 0)}` : ""}</span>
          </div>
          <Progress value={used} tone={used > 66 ? "error" : used > 33 ? "warning" : "ok"} />
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <Badge tone={streak >= maxStreak ? "error" : "neutral"}>loss streak {streak}/{maxStreak}</Badge>
          <Badge tone={rec.data?.blocking_entries ? "error" : "neutral"}>{rec.data?.blockers.length ?? 0} unreconciled</Badge>
          {r.kill_switch.halted && <Badge tone="error">kill switch on</Badge>}
          {r.realized_source !== "supabase" && <Badge tone="warning">trade history unavailable</Badge>}
        </div>
      </div>
    </Card>
  );
}
