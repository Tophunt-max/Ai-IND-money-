"use client";

import { useState } from "react";

import { Badge, Button, Card, ErrorBox, Loading, PageTitle } from "@/components/ui";
import { api, BACKEND_URL, clearToken, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Halt {
  halted: boolean;
  reason: string;
  since: string;
  source: string;
  text: string;
  warning?: string;
}

export default function SettingsPage() {
  const ks = useApi<Halt>("/api/dashboard/kill-switch");
  const status = useApi<{ version: string; mode: string }>("/api/status");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = async (path: string, body?: unknown) => {
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const res = await api<Halt>(path, { method: "POST", json: body ?? {} });
      ks.setData(res);
      setMsg(res.warning || (res.halted ? "Trading halted." : "Trading resumed."));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const halt = () => {
    if (!confirm("Stop all new BUY orders everywhere (scheduler, chat, Telegram)? Sells stay allowed.")) return;
    act("/api/dashboard/kill-switch/halt", { reason: reason.trim() || "halted from the dashboard" });
  };

  const k = ks.data;
  return (
    <div className="space-y-5">
      <PageTitle title="Settings" />

      <Card
        title="Kill switch"
        right={k && <Badge tone={k.halted ? "error" : "ok"}>{k.halted ? "HALTED" : "Active"}</Badge>}
      >
        <ErrorBox error={ks.error || error} />
        {msg && <div className="text-sm text-blue-300 mb-3">{msg}</div>}
        {!k ? (
          <Loading />
        ) : k.halted ? (
          <div className="space-y-3">
            <p className="text-sm text-red-300">{k.text}</p>
            {k.since && <p className="text-xs text-gray-500">Since {when(k.since)} · source: {k.source}</p>}
            {k.source === "env" ? (
              <p className="text-xs text-yellow-400">
                Set by SKOPAQ_TRADING_HALTED in ENV_FILE: remove it there and redeploy to resume.
              </p>
            ) : (
              <Button onClick={() => act("/api/dashboard/kill-switch/resume")} disabled={busy}>
                {busy ? "..." : "Resume trading"}
              </Button>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-gray-400">
              Trading is active. Halting rejects every new BUY in the scheduler, chat, Telegram and
              CLI. Selling (closing positions) stays allowed.
            </p>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Reason (optional)"
              maxLength={300}
              className="w-full bg-gray-900 border border-gray-700 rounded-md px-3 py-2 text-sm"
            />
            <Button variant="danger" onClick={halt} disabled={busy}>
              {busy ? "..." : "🛑 Halt trading"}
            </Button>
          </div>
        )}
      </Card>

      <Card title="Connection">
        <dl className="text-sm space-y-2">
          <div className="flex justify-between gap-3">
            <dt className="text-gray-500">Backend</dt>
            <dd className="break-all text-right">{BACKEND_URL}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-gray-500">Version</dt>
            <dd>{status.data?.version || "—"}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-gray-500">Mode</dt>
            <dd>{status.data?.mode?.toUpperCase() || "—"}</dd>
          </div>
        </dl>
        <p className="text-xs text-gray-500 mt-4">
          Mode, keys and live trading are changed in the ENV_FILE GitHub secret, then
          Actions → Deploy (EC2) → Run workflow.
        </p>
      </Card>

      <Button
        variant="ghost"
        onClick={() => {
          clearToken();
          window.location.href = "/";
        }}
      >
        Logout from this device
      </Button>
    </div>
  );
}
