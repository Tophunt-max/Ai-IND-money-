"use client";

import {
  ChevronRight, History, LogOut, OctagonX, Play, PlugZap, Server, Settings, ShieldCheck,
  SlidersHorizontal, Smartphone, User,
} from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { useAuth } from "@/components/AuthGate";
import ModeSwitch, { useModeChanged } from "@/components/ModeSwitch";
import { Badge, Button, Card, Empty, ErrorBox, KV, Notice, PageTitle, Segmented, Skeleton } from "@/components/ui";
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
    <Card title="Account" icon={User} right={<Badge tone={isAdmin ? "ok" : "neutral"}>{user.role.toUpperCase()}</Badge>}>
      <div className="mb-5 flex items-center gap-4">
        <div className="grid h-12 w-12 place-items-center rounded-full bg-gradient-to-br from-brand-500 to-cyan-400 text-lg font-semibold uppercase text-white">
          {(user.name || user.email).slice(0, 1)}
        </div>
        <div className="min-w-0">
          <div className="truncate font-medium text-white">{user.name || user.email}</div>
          <div className="truncate text-xs text-gray-500">{user.email} · {user.provider || user.via}</div>
        </div>
      </div>
      {!isAdmin && <p className="mb-4 text-xs text-gray-500">View-only: you can see everything, but not run analyses, trades, scans, chat or the kill switch.</p>}
      {user.provider === "email" && (
        <form onSubmit={changePassword} className="flex gap-2">
          <input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)}
            placeholder="New password" className="field" />
          <Button type="submit" variant="ghost" disabled={!pw}>Change</Button>
        </form>
      )}
      {pwMsg && <p className="mt-2 text-sm text-emerald-300">{pwMsg}</p>}
      <ErrorBox error={pwErr} />
      <div className="mt-4 grid grid-cols-2 gap-2">
        <Button variant="ghost" icon={LogOut} onClick={() => signOut(false)}>Logout</Button>
        <Button variant="ghost" onClick={logoutAll} className="text-rose-300">Logout all devices</Button>
      </div>
    </Card>
  );
}

function LoginHistory() {
  const { isAdmin } = useAuth();
  const [scope, setScope] = useState<"mine" | "all">("mine");
  const logins = useApi<{ logins: Login[] }>(`/api/dashboard/auth/logins?limit=30&scope=${scope}`);
  return (
    <Card title="Login history" icon={History}
      right={isAdmin && <Segmented size="sm" value={scope} onChange={setScope} options={[{ value: "mine", label: "Mine" }, { value: "all", label: "All users" }]} />}>
      <ErrorBox error={logins.error} />
      {logins.loading && !logins.data ? <Skeleton className="h-24" /> : (logins.data?.logins.length || 0) === 0 ? (
        <Empty>No logins recorded yet.</Empty>
      ) : (
        <div className="-mx-1 max-h-80 divide-y divide-white/[0.04] overflow-y-auto">
          {logins.data!.logins.map((l, i) => (
            <div key={i} className="flex items-start justify-between gap-3 px-1 py-2.5 text-sm">
              <div className="min-w-0">
                <div className="text-gray-200">{device(l.user_agent)} {l.provider && <span className="text-xs text-gray-500">· {l.provider}</span>}</div>
                <div className="truncate text-xs text-gray-500">{scope === "all" && `${l.email} · `}IP {l.ip || "?"}</div>
              </div>
              <div className="shrink-0 text-right">
                <Badge tone={l.status === "ok" ? "ok" : "error"}>{l.status === "ok" ? "Login" : "Denied"}</Badge>
                <div className="mt-1 text-[11px] text-gray-500">{when(l.created_at)}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
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
  useModeChanged(status.reload);
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
  const links = [
    { href: "/broker", label: "Broker connections", text: "INDstocks token (equity and F&O)", icon: PlugZap },
    ...(isAdmin ? [{ href: "/settings/environment", label: "Environment", text: "Mode, keys, live trading", icon: SlidersHorizontal }] : []),
  ];

  return (
    <div className="space-y-6">
      <PageTitle title="Settings" icon={Settings} subtitle="Account, safety and connections" />

      <ModeSwitch />

      <Card title="Kill switch" icon={ShieldCheck}
        className={k?.halted ? "border-rose-500/30" : ""}
        right={k && <Badge tone={k.halted ? "error" : "ok"} dot>{k.halted ? "HALTED" : "Trading active"}</Badge>}>
        <ErrorBox error={ks.error || error} />
        {msg && <Notice tone="info" className="mb-4">{msg}</Notice>}
        {!k ? <Skeleton className="h-16" /> : k.halted ? (
          <div className="space-y-3">
            <Notice tone="error" icon={OctagonX} title="All new BUYs are blocked">{k.text}</Notice>
            {k.since && <p className="text-xs text-gray-500">Since {when(k.since)} · source: {k.source}</p>}
            {!isAdmin ? <p className="text-xs text-gray-500">Only an admin can resume trading.</p>
              : k.source === "env" ? <p className="text-xs text-amber-300">Set by SKOPAQ_TRADING_HALTED in ENV_FILE: remove it there and redeploy to resume.</p>
              : <Button icon={Play} variant="success" loading={busy} onClick={() => act("/api/dashboard/kill-switch/resume")}>Resume trading</Button>}
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-gray-400">Halting rejects every new BUY in the scheduler, chat, Telegram and CLI. Selling (closing positions) stays allowed.</p>
            {isAdmin ? (
              <div className="flex flex-col gap-2 sm:flex-row">
                <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (optional)" maxLength={300} className="field" />
                <Button variant="danger" icon={OctagonX} loading={busy} onClick={halt} className="shrink-0">Halt trading</Button>
              </div>
            ) : <p className="text-xs text-gray-500">Only an admin can halt trading.</p>}
          </div>
        )}
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Account />
        <div className="space-y-5">
          <Card title="Connections" icon={Server}>
            <KV items={[
              ["Backend", <span key="b" className="break-all text-xs">{BACKEND_URL}</span>],
              ["Version", status.data?.version || "—"],
              ["Mode", status.data ? <Badge key="m" tone={status.data.mode === "live" ? "error" : "warning"} dot>{status.data.mode.toUpperCase()}</Badge> : "—"],
            ]} />
            <div className="mt-4 space-y-2">
              {links.map((l) => (
                <Link key={l.href} href={l.href} className="flex items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 transition hover:border-brand-500/30">
                  <l.icon className="h-5 w-5 text-brand-300" />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-gray-100">{l.label}</div>
                    <div className="text-xs text-gray-500">{l.text}</div>
                  </div>
                  <ChevronRight className="h-4 w-4 text-gray-600" />
                </Link>
              ))}
            </div>
          </Card>
          <Card title="Install as an app" icon={Smartphone}>
            <ul className="space-y-1.5 text-sm text-gray-400">
              <li><b className="text-gray-200">Android</b> (Chrome): ⋮ menu → Install app</li>
              <li><b className="text-gray-200">iPhone</b> (Safari): Share → Add to Home Screen</li>
              <li><b className="text-gray-200">Computer</b> (Chrome/Edge): install icon in the address bar</li>
            </ul>
          </Card>
        </div>
      </div>

      <LoginHistory />
    </div>
  );
}
