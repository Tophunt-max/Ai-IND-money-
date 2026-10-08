"use client";

import { AlertTriangle, CheckCircle2, CircleDashed, NotebookPen, ShieldAlert, ToggleRight, X, XCircle, Zap } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { useApi } from "@/lib/hooks";
import { LIVE_SETTINGS, modes, PAPER_SETTINGS, saveEnv, type EnvData } from "@/lib/env";

import { useAuth } from "./AuthGate";
import { Badge, Button, Card, cx, ErrorBox, Notice, Skeleton } from "./ui";

interface Broker {
  indstocks: { valid: boolean; source: string; remaining_seconds: number | null };
}

function Check({ ok, children }: { ok: boolean | null; children: React.ReactNode }) {
  const Icon = ok === null ? CircleDashed : ok ? CheckCircle2 : XCircle;
  return (
    <li className="flex items-start gap-2.5 text-sm">
      <Icon className={cx("mt-0.5 h-4 w-4 shrink-0", ok === null ? "text-gray-500" : ok ? "text-emerald-400" : "text-rose-400")} />
      <span className="text-gray-300">{children}</span>
    </li>
  );
}

/** Confirmation for real money: a checklist, an acknowledgement and typing LIVE. */
function LiveDialog({ broker, onCancel, onConfirm, busy, error }: {
  broker: Broker | null;
  onCancel: () => void;
  onConfirm: () => void;
  busy: boolean;
  error: string | null;
}) {
  const [typed, setTyped] = useState("");
  const [ack, setAck] = useState(false);
  const token = broker?.indstocks.valid ?? null;
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;
  // A portal: a transformed ancestor (the card's fade-in) would trap a fixed element
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={busy ? undefined : onCancel} />
      <div className="relative max-h-[92dvh] w-full max-w-md overflow-y-auto rounded-t-3xl border border-rose-500/30 bg-ink-900 p-6 pb-[calc(1.5rem+env(safe-area-inset-bottom))] shadow-2xl animate-fade-in sm:rounded-3xl">
        <button onClick={onCancel} disabled={busy} className="absolute right-4 top-4 rounded-lg p-1.5 text-gray-400 hover:bg-white/[0.06]">
          <X className="h-5 w-5" />
        </button>
        <div className="mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-rose-500/15 ring-1 ring-rose-500/30">
          <ShieldAlert className="h-6 w-6 text-rose-300" />
        </div>
        <h3 className="text-lg font-semibold text-white">Switch to LIVE trading?</h3>
        <p className="mt-1 text-sm text-gray-400">
          The scheduler will place <b className="text-rose-300">real orders with real money</b> on INDstocks from its
          next session (09:15 IST), without supervision. A session already running keeps its mode.
        </p>

        <ul className="mt-5 space-y-2.5 rounded-2xl bg-white/[0.03] p-4 ring-1 ring-white/[0.06]">
          <Check ok={token}>
            INDstocks token {token === null ? "…" : token ? "valid" : <><b>not valid</b> — <Link href="/broker" className="link">set it</Link> before 08:45</>}
          </Check>
          <Check ok={null}>Server IP whitelisted at INDstocks</Check>
          <Check ok={null}>Paper-traded for at least a week and checked the Track record</Check>
          <Check ok={true}>Safety limits stay on: 15% per position, 3% daily loss, max 5 positions, kill switch</Check>
        </ul>

        <label className="mt-4 flex items-start gap-2.5 text-sm text-gray-300">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5 h-4 w-4 accent-rose-500" />
          I understand I can lose money and I am responsible for every order.
        </label>
        <div className="mt-4">
          <label className="label">Type <b className="text-rose-300">LIVE</b> to confirm</label>
          <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" className="field font-mono tracking-widest" placeholder="LIVE" />
        </div>
        {error && <div className="mt-4"><ErrorBox error={error} /></div>}
        <div className="mt-5 grid grid-cols-2 gap-2">
          <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant="danger" icon={Zap} loading={busy} disabled={!ack || typed.trim() !== "LIVE"} onClick={onConfirm}>Go LIVE</Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Call *fn* after a paper / live switch from any ModeSwitch. */
export function useModeChanged(fn: () => void) {
  useEffect(() => {
    window.addEventListener("aiind:mode-changed", fn);
    return () => window.removeEventListener("aiind:mode-changed", fn);
  }, [fn]);
}

/** Paper / live switch (admins). Pass *env* and *onChange* to share data with a page. */
export default function ModeSwitch({ env, onChange, compact = false }: {
  env?: EnvData | null;
  onChange?: (data: EnvData) => void;
  compact?: boolean;
}) {
  const { isAdmin } = useAuth();
  const own = useApi<EnvData>(isAdmin && env === undefined ? "/api/dashboard/settings/env" : null);
  const broker = useApi<Broker>(isAdmin ? "/api/dashboard/broker" : null);
  const [dialog, setDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  if (!isAdmin) return null;
  const data = env === undefined ? own.data : env;
  const m = data ? modes(data.settings) : null;

  const apply = async (live: boolean) => {
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const res = await saveEnv({ set: live ? LIVE_SETTINGS : PAPER_SETTINGS, confirm_live: live });
      if (env === undefined) own.setData(res);
      onChange?.(res);
      window.dispatchEvent(new Event("aiind:mode-changed"));
      setDialog(false);
      setMsg(live ? "LIVE trading is on: real orders from the next session. Telegram was notified."
        : "Back to PAPER: the next session trades simulated orders only.");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const goPaper = () => {
    if (!confirm("Switch to PAPER trading? The next session places simulated orders only. A session already running keeps its mode.")) return;
    apply(false);
  };

  return (
    <Card title="Trading mode" icon={ToggleRight}
      className={m?.live ? "border-rose-500/30 bg-gradient-to-br from-rose-500/[0.07] to-transparent" : ""}
      right={m && (m.live ? <Badge tone="error" dot>LIVE · real money</Badge> : <Badge tone="warning" dot>PAPER · simulated</Badge>)}>
      <ErrorBox error={own.error || (dialog ? null : error)} />
      {!m ? <Skeleton className="h-16" /> : (
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="grid grid-cols-2 gap-3 text-sm sm:max-w-md sm:flex-1">
            <div className="rounded-xl bg-white/[0.03] p-3 ring-1 ring-white/[0.06]">
              <div className="text-xs text-gray-500">Manual trades & chat</div>
              <div className={cx("mt-1 font-semibold", m.trading === "live" ? "text-rose-300" : "text-amber-300")}>{m.trading.toUpperCase()}</div>
            </div>
            <div className="rounded-xl bg-white/[0.03] p-3 ring-1 ring-white/[0.06]">
              <div className="text-xs text-gray-500">Auto-trading (scheduler)</div>
              <div className={cx("mt-1 font-semibold", m.scheduler === "live" && m.confirmed ? "text-rose-300" : "text-amber-300")}>
                {m.scheduler.toUpperCase()}{m.scheduler === "live" && !m.confirmed && <span className="ml-1 text-xs font-normal text-amber-300">(not confirmed)</span>}
              </div>
            </div>
          </div>
          <div className="flex flex-col gap-2 sm:items-end">
            {m.live ? (
              <Button variant="ghost" icon={NotebookPen} loading={busy} onClick={goPaper}>Switch to PAPER</Button>
            ) : (
              <Button variant="danger" icon={Zap} onClick={() => { setError(null); setDialog(true); }}>Switch to LIVE</Button>
            )}
            {broker.data && !broker.data.indstocks.valid && (
              <span className="flex items-center gap-1.5 text-xs text-amber-300"><AlertTriangle className="h-3.5 w-3.5" /> INDstocks token not valid</span>
            )}
          </div>
        </div>
      )}
      {msg && <Notice tone={m?.live ? "error" : "ok"} className="mt-4">{msg}</Notice>}
      {!compact && (
        <p className="mt-4 text-xs text-gray-500">
          Saved as dashboard overrides (they win over ENV_FILE). The API uses them at once; the scheduler from
          its next session. Every switch is logged and sent to Telegram.
        </p>
      )}
      {dialog && <LiveDialog broker={broker.data} busy={busy} error={error} onCancel={() => setDialog(false)} onConfirm={() => apply(true)} />}
    </Card>
  );
}
