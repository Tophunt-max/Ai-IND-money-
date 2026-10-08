"use client";

import { CheckCircle2, ExternalLink, KeyRound, Link2, PlugZap, RefreshCw, Trash2, XCircle } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, ErrorBox, Field, KV, Notice, PageTitle, Select, Skeleton } from "@/components/ui";
import { api, BACKEND_URL, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface BrokerStatus {
  mode: string;
  indstocks: {
    valid: boolean;
    expires_at: string | null;
    remaining_seconds: number | null;
    warning: string;
    source: "stored" | "env" | "none";
    stored: boolean;
    env_token: boolean;
  };
  kite: { configured: boolean; connected: boolean; login_url: string | null };
}

function remaining(sec: number | null): string {
  if (sec == null) return "—";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function StatusIcon({ ok }: { ok: boolean }) {
  return ok ? <CheckCircle2 className="h-5 w-5 text-emerald-400" /> : <XCircle className="h-5 w-5 text-rose-400" />;
}

export default function BrokerPage() {
  const { isAdmin } = useAuth();
  const st = useApi<BrokerStatus>("/api/dashboard/broker", 60000);
  const [token, setToken] = useState("");
  const [ttl, setTtl] = useState(24);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const res = await api<BrokerStatus>("/api/dashboard/broker/indstocks-token", {
        method: "POST", json: { token: token.trim(), ttl_hours: ttl },
      });
      st.setData(res);
      setToken("");
      setMsg(res.indstocks.valid ? "Token saved. The scheduler uses it from its next session." : "Saved, but the token is not valid.");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    if (!confirm("Delete the stored INDstocks token? Live trading and the auto-trading session stop working until a new one is set.")) return;
    setBusy(true);
    setError(null);
    try {
      st.setData(await api<BrokerStatus>("/api/dashboard/broker/indstocks-token", { method: "DELETE" }));
      setMsg("Stored token deleted.");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const s = st.data;
  const ind = s?.indstocks;
  const kiteLogin = s?.kite.login_url || `${BACKEND_URL}/api/kite/login`;

  return (
    <div className="space-y-6">
      <PageTitle title="Broker connections" icon={PlugZap} subtitle="INDstocks executes the trades; Kite is optional for market data"
        right={<Button variant="ghost" icon={RefreshCw} onClick={st.reload}>Refresh</Button>} />
      <ErrorBox error={st.error || error} />
      {msg && <Notice tone="ok">{msg}</Notice>}

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="INDstocks" icon={KeyRound} subtitle="API token, renewed every trading day"
          right={ind && <Badge tone={ind.valid ? "ok" : "error"} dot>{ind.valid ? "Connected" : "No token"}</Badge>}>
          {!ind ? <div className="space-y-3"><Skeleton /><Skeleton className="h-4 w-2/3" /></div> : (
            <div className="space-y-5">
              <div className="flex items-center gap-4 rounded-xl bg-white/[0.03] p-4 ring-1 ring-white/[0.06]">
                <StatusIcon ok={ind.valid} />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium text-white">{ind.valid ? "Token valid" : "No valid token"}</div>
                  <div className="text-xs text-gray-500">
                    {ind.valid ? (ind.source === "env" ? "From SKOPAQ_INDSTOCKS_TOKEN (expiry unknown)" : `Expires in ${remaining(ind.remaining_seconds)}`) : ind.warning}
                  </div>
                </div>
                {ind.source === "stored" && ind.remaining_seconds != null && (
                  <div className="num text-right text-2xl font-semibold text-white">{remaining(ind.remaining_seconds)}</div>
                )}
              </div>
              <KV items={[
                ["Source", ind.source === "stored" ? "Stored (encrypted on the server)" : ind.source === "env" ? "Environment variable" : "—"],
                ["Expires at", ind.expires_at ? when(ind.expires_at) : "—"],
                ["Env token present", ind.env_token ? "Yes" : "No"],
              ]} />
              {ind.warning && ind.valid && <Notice tone="warning">{ind.warning}</Notice>}

              {isAdmin ? (
                <form onSubmit={save} className="space-y-3 border-t border-white/[0.06] pt-5">
                  <Field label="New token" hint="INDstocks app/web → API → generate today's token. Stored encrypted, never shown again.">
                    <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)}
                      placeholder="Paste the bearer token" className="field font-mono" />
                  </Field>
                  <div className="flex flex-wrap items-end gap-3">
                    <Field label="Valid for" className="w-36">
                      <Select value={ttl} onChange={(e) => setTtl(Number(e.target.value))}>
                        {[8, 12, 24, 48].map((h) => <option key={h} value={h}>{h} hours</option>)}
                      </Select>
                    </Field>
                    <Button type="submit" icon={KeyRound} loading={busy} disabled={token.trim().length < 10}>Save token</Button>
                    {ind.stored && <Button variant="ghost" icon={Trash2} onClick={clear} disabled={busy}>Delete stored</Button>}
                  </div>
                </form>
              ) : <p className="text-xs text-gray-500">Only an admin can change the token.</p>}
            </div>
          )}
        </Card>

        <Card title="Zerodha Kite" icon={Link2} subtitle="Optional: market data, options chain, GTT, mutual funds"
          right={s && <Badge tone={s.kite.connected ? "ok" : s.kite.configured ? "warning" : "neutral"} dot>
            {s.kite.connected ? "Connected" : s.kite.configured ? "Logged out" : "Not set up"}</Badge>}>
          {!s ? <div className="space-y-3"><Skeleton /><Skeleton className="h-4 w-2/3" /></div> : (
            <div className="space-y-5">
              <div className="flex items-center gap-4 rounded-xl bg-white/[0.03] p-4 ring-1 ring-white/[0.06]">
                <StatusIcon ok={s.kite.connected} />
                <div>
                  <div className="text-sm font-medium text-white">
                    {s.kite.connected ? "Session active until 06:00 IST" : s.kite.configured ? "Log in once a day" : "SKOPAQ_KITE_API_KEY is not set"}
                  </div>
                  <div className="text-xs text-gray-500">Kite sessions expire every morning.</div>
                </div>
              </div>
              {s.kite.configured && (
                <a href={kiteLogin} target="_blank" rel="noreferrer">
                  <Button icon={ExternalLink} className="w-full">{s.kite.connected ? "Log in again" : "Log in to Kite"}</Button>
                </a>
              )}
              {!s.kite.login_url && s.kite.configured && (
                <p className="text-xs text-gray-500">
                  Set SKOPAQ_PUBLIC_BASE_URL (Environment page) to the API&apos;s public URL, and expose
                  /api/kite/* in the reverse proxy, so the Zerodha redirect reaches the server. Telegram /login works too.
                </p>
              )}
              <Notice tone="warning" title="Kite order tools">
                With a Kite session, the Claude Code (MCP) order tools can place real Zerodha orders outside
                the safety checks. The dashboard only reads from Kite.
              </Notice>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
