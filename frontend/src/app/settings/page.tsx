"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, Empty, ErrorBox, Loading, PageTitle } from "@/components/ui";
import { api, BACKEND_URL, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { supabase } from "@/lib/supabase";

interface Login {
  email: string;
  role: string | null;
  status: string;
  provider: string | null;
  ip: string | null;
  user_agent: string | null;
  created_at: string;
}

function device(ua: string | null): string {
  if (!ua) return "Unknown device";
  const os = /Android/i.test(ua) ? "Android" : /iPhone|iPad/i.test(ua) ? "iPhone/iPad"
    : /Windows/i.test(ua) ? "Windows" : /Mac OS/i.test(ua) ? "Mac" : /Linux/i.test(ua) ? "Linux" : "Other";
  const br = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox"
    : /Safari\//.test(ua) ? "Safari" : "Browser";
  return `${br} on ${os}`;
}

function Account() {
  const { user, isAdmin, signOut } = useAuth();
  const [scope, setScope] = useState<"mine" | "all">("mine");
  const logins = useApi<{ logins: Login[] }>(`/api/dashboard/auth/logins?limit=30&scope=${scope}`);
  const [pw, setPw] = useState("");
  const [pwMsg, setPwMsg] = useState<string | null>(null);
  const [pwErr, setPwErr] = useState<string | null>(null);

  const changePassword = async (e: FormEvent) => {
    e.preventDefault();
    setPwMsg(null);
    setPwErr(null);
    if (pw.length < 8) return setPwErr("Use at least 8 characters.");
    const { error } = await supabase()!.auth.updateUser({ password: pw });
    if (error) return setPwErr(error.message);
    setPw("");
    setPwMsg("Password changed.");
  };

  const logoutAll = async () => {
    if (!confirm("Log out of the dashboard on every device (this one too)?")) return;
    await signOut(true);
  };

  if (!user) return null;
  return (
    <>
      <Card title="👤 Account" right={<Badge tone={isAdmin ? "ok" : "neutral"}>{user.role.toUpperCase()}</Badge>}>
        <dl className="text-sm space-y-2">
          <div className="flex justify-between gap-3"><dt className="text-gray-500">Email</dt><dd className="break-all">{user.email}</dd></div>
          {user.name && <div className="flex justify-between"><dt className="text-gray-500">Name</dt><dd>{user.name}</dd></div>}
          <div className="flex justify-between"><dt className="text-gray-500">Signed in with</dt><dd>{user.provider || user.via}</dd></div>
        </dl>
        {!isAdmin && (
          <p className="text-xs text-gray-500 mt-3">View-only: you can see everything, but not run analyses, trades, scans, chat or the kill switch.</p>
        )}
        {user.provider === "email" && (
          <form onSubmit={changePassword} className="flex gap-2 mt-4">
            <input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)}
              placeholder="New password" className="flex-1 bg-gray-900 border border-gray-700 rounded-md px-3 py-2 text-sm" />
            <Button type="submit" variant="ghost" disabled={!pw}>Change</Button>
          </form>
        )}
        {pwMsg && <p className="text-sm text-green-400 mt-2">{pwMsg}</p>}
        <ErrorBox error={pwErr} />
        <div className="grid grid-cols-2 gap-2 mt-4">
          <Button variant="ghost" onClick={() => signOut(false)}>Logout</Button>
          <Button variant="danger" onClick={logoutAll}>Logout all devices</Button>
        </div>
      </Card>

      <Card
        title="🕒 Login history"
        right={isAdmin && (
          <select value={scope} onChange={(e) => setScope(e.target.value as "mine" | "all")}
            className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs">
            <option value="mine">Mine</option>
            <option value="all">All users</option>
          </select>
        )}
      >
        <ErrorBox error={logins.error} />
        {logins.loading && !logins.data ? <Loading /> : (logins.data?.logins.length || 0) === 0 ? (
          <Empty>No logins recorded yet.</Empty>
        ) : (
          <div className="divide-y divide-gray-800">
            {logins.data!.logins.map((l, i) => (
              <div key={i} className="py-2 flex items-start justify-between gap-3 text-sm">
                <div>
                  <div>{device(l.user_agent)} {l.provider && <span className="text-xs text-gray-500">· {l.provider}</span>}</div>
                  <div className="text-xs text-gray-500">{scope === "all" && `${l.email} · `}IP {l.ip || "?"}</div>
                </div>
                <div className="text-right shrink-0">
                  <Badge tone={l.status === "ok" ? "ok" : "error"}>{l.status === "ok" ? "Login" : "Denied"}</Badge>
                  <div className="text-xs text-gray-500 mt-1">{when(l.created_at)}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}

interface Halt {
  halted: boolean;
  reason: string;
  since: string;
  source: string;
  text: string;
  warning?: string;
}

export default function SettingsPage() {
  const { isAdmin } = useAuth();
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
      <Account />

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
            {!isAdmin ? (
              <p className="text-xs text-gray-500">Only an admin can resume trading.</p>
            ) : k.source === "env" ? (
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
            {isAdmin ? (<>
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
            </>) : <p className="text-xs text-gray-500">Only an admin can halt trading.</p>}
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

      <Card title="More">
        <div className="grid grid-cols-2 gap-2 text-sm">
          <Link href="/scheduler" className="border border-gray-800 rounded p-3 hover:bg-gray-900">⏰ Scheduler & logs</Link>
          <Link href="/scanner" className="border border-gray-800 rounded p-3 hover:bg-gray-900">🔎 Scanner</Link>
        </div>
      </Card>

      <Card title="📱 Install as an app">
        <ul className="text-sm text-gray-400 space-y-1 list-disc pl-5">
          <li>Android (Chrome): ⋮ menu → <b>Install app</b> / <b>Add to Home screen</b></li>
          <li>iPhone (Safari): Share → <b>Add to Home Screen</b></li>
          <li>Computer (Chrome/Edge): install icon in the address bar</li>
        </ul>
      </Card>

    </div>
  );
}
