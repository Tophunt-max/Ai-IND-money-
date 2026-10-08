"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, Empty, ErrorBox, Loading, PageTitle } from "@/components/ui";
import { api, ApiError, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Setting {
  key: string;
  kind: "bool" | "int" | "float" | "choice" | "text";
  secret: boolean;
  choices: string[];
  default: string;
  group: string;
  help: string;
  locked: string;
  source: "dashboard" | "server" | "default";
  is_set: boolean;
  value: string | null;
  server_set: boolean;
}

interface HistoryEntry {
  at: string;
  by: string;
  set: Record<string, string>;
  removed: string[];
  live: string[];
}

interface EnvData {
  settings: Setting[];
  file: string;
  history: HistoryEntry[];
  changed?: string[];
  removed?: string[];
  live?: string[];
}

const GROUP_ORDER = [
  "Trading mode", "Scheduler", "Broker (INDstocks)", "Telegram", "Daemon, scanner & monitor",
  "Risk & sizing", "AI models & keys", "Kite (Zerodha)", "Other",
];

const INPUT = "w-full bg-gray-900 border border-gray-700 rounded-md px-3 py-2 text-sm";

const SOURCE_LABEL: Record<Setting["source"], string> = {
  dashboard: "DASHBOARD",
  server: "ENV_FILE",
  default: "DEFAULT",
};

function shown(s: Setting): string {
  if (s.secret) return s.is_set ? "•••••••• (set)" : "not set";
  return s.value === "" || s.value === null ? "(empty)" : s.value;
}

/** POST a change; on 409 (turns live trading on) ask to type LIVE and send it again. */
async function send(body: { set?: Record<string, string>; remove?: string[] }): Promise<EnvData | null> {
  try {
    return await api<EnvData>("/api/dashboard/settings/env", { method: "POST", json: body });
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 409) throw e;
    const typed = prompt(
      `⚠️ REAL MONEY\n\n${e.message}\n\nThe scheduler will place real orders on INDstocks from ` +
        "its next session, without supervision.\n\nType LIVE to confirm:",
    );
    if ((typed || "").trim() !== "LIVE") return null;
    return await api<EnvData>("/api/dashboard/settings/env", {
      method: "POST",
      json: { ...body, confirm_live: true },
    });
  }
}

function Editor({ s, onSave, onCancel, busy }: {
  s: Setting;
  onSave: (value: string) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [value, setValue] = useState<string>(s.secret ? "" : s.value ?? "");
  const options = s.kind === "bool" ? ["true", "false"] : s.choices;
  return (
    <div className="mt-2 space-y-2">
      {options.length > 0 ? (
        <select value={value} onChange={(e) => setValue(e.target.value)} className={INPUT}>
          {!options.includes(value) && <option value={value}>{value || "(choose)"}</option>}
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : (
        <input
          type={s.secret ? "password" : "text"}
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={s.secret ? "New value (the current one is never shown)" : s.default ? `Default: ${s.default}` : ""}
          inputMode={s.kind === "int" || s.kind === "float" ? "decimal" : undefined}
          className={INPUT}
        />
      )}
      <div className="flex gap-2">
        <Button onClick={() => onSave(value)} disabled={busy || (s.secret && !value)}>
          {busy ? "..." : "Save"}
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
      </div>
    </div>
  );
}

function Row({ s, editing, setEditing, save, remove, busy }: {
  s: Setting;
  editing: boolean;
  setEditing: (key: string | null) => void;
  save: (key: string, value: string) => void;
  remove: (key: string) => void;
  busy: boolean;
}) {
  return (
    <div className="py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-mono text-xs break-all text-gray-200">{s.key}</div>
          {s.help && <div className="text-xs text-gray-500 mt-0.5">{s.help}</div>}
          <div className={`text-sm mt-1 break-all ${s.secret ? "text-gray-400" : ""}`}>{shown(s)}</div>
          {s.locked && <div className="text-xs text-yellow-500 mt-1">🔒 {s.locked}: ENV_FILE only</div>}
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <Badge tone={s.source === "dashboard" ? "warning" : s.source === "server" ? "ok" : "neutral"}>
            {SOURCE_LABEL[s.source]}
          </Badge>
          {!s.locked && !editing && (
            <div className="flex gap-1">
              <button onClick={() => setEditing(s.key)} className="text-xs text-blue-400 hover:underline">
                Edit
              </button>
              {s.source === "dashboard" && (
                <button onClick={() => remove(s.key)} disabled={busy}
                  className="text-xs text-red-400 hover:underline ml-2">
                  Reset
                </button>
              )}
            </div>
          )}
        </div>
      </div>
      {editing && (
        <Editor s={s} busy={busy} onCancel={() => setEditing(null)} onSave={(v) => save(s.key, v)} />
      )}
    </div>
  );
}

function ModeCard({ byKey, busy, run }: {
  byKey: Record<string, Setting>;
  busy: boolean;
  run: (body: { set?: Record<string, string>; remove?: string[] }, ok: string) => void;
}) {
  const mode = byKey["SKOPAQ_TRADING_MODE"]?.value || "paper";
  const sched = byKey["SKOPAQ_SCHEDULER_MODE"]?.value || "paper";
  const confirmed = byKey["SKOPAQ_SCHEDULER_CONFIRM_LIVE"]?.value === "true";
  const autoLive = sched === "live" && confirmed;
  const token = byKey["SKOPAQ_INDSTOCKS_TOKEN"];
  const live = mode === "live" || autoLive;

  const goLive = () =>
    run({ set: { SKOPAQ_TRADING_MODE: "live", SKOPAQ_SCHEDULER_MODE: "live", SKOPAQ_SCHEDULER_CONFIRM_LIVE: "true" } },
      "Live trading is on: the scheduler trades real money from its next session.");
  const goPaper = () => {
    if (!confirm("Switch back to PAPER? The scheduler uses it from its next session; a session already running keeps its mode.")) return;
    run({ set: { SKOPAQ_TRADING_MODE: "paper", SKOPAQ_SCHEDULER_MODE: "paper", SKOPAQ_SCHEDULER_CONFIRM_LIVE: "false" } },
      "Back to paper trading.");
  };

  return (
    <Card title="Trading mode" right={<Badge tone={live ? "error" : "ok"}>{live ? "LIVE" : "PAPER"}</Badge>}>
      <dl className="text-sm space-y-2">
        <div className="flex justify-between"><dt className="text-gray-500">Trading mode</dt><dd>{mode.toUpperCase()}</dd></div>
        <div className="flex justify-between">
          <dt className="text-gray-500">Auto-trading (scheduler)</dt>
          <dd>{sched.toUpperCase()}{sched === "live" && !confirmed && <span className="text-yellow-400"> (not confirmed: sessions skipped)</span>}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-gray-500">INDstocks token (env)</dt>
          <dd>{token?.is_set ? "set" : "not set (or set with `skopaq token set`)"}</dd>
        </div>
      </dl>
      <div className="mt-4">
        {live ? (
          <Button variant="ghost" onClick={goPaper} disabled={busy}>Switch to PAPER</Button>
        ) : (
          <Button variant="danger" onClick={goLive} disabled={busy}>🔴 Switch to LIVE (real money)</Button>
        )}
      </div>
      <p className="text-xs text-gray-500 mt-3">
        Before going live: paper-trade at least a week, set today&apos;s INDstocks token, and whitelist the
        server&apos;s IP at INDstocks. The safety limits (position size, daily loss, …) always apply.
      </p>
    </Card>
  );
}

export default function EnvSettingsPage() {
  const { isAdmin } = useAuth();
  const env = useApi<EnvData>(isAdmin ? "/api/dashboard/settings/env" : null);
  const [editing, setEditing] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newKey, setNewKey] = useState("");

  const settings = env.data?.settings ?? [];
  const byKey = useMemo(() => Object.fromEntries(settings.map((s) => [s.key, s])), [settings]);
  const groups = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const out: Record<string, Setting[]> = {};
    for (const s of settings) {
      if (q && !s.key.toLowerCase().includes(q) && !s.help.toLowerCase().includes(q)) continue;
      (out[s.group] ||= []).push(s);
    }
    return GROUP_ORDER.filter((g) => out[g]).map((g) => [g, out[g]] as const);
  }, [settings, filter]);
  const overridden = settings.filter((s) => s.source === "dashboard");

  const run = async (body: { set?: Record<string, string>; remove?: string[] }, ok?: string) => {
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const res = await send(body);
      if (!res) return setMsg("Not changed.");
      env.setData(res);
      setEditing(null);
      const parts = [
        res.changed?.length ? `Saved ${res.changed.join(", ")}` : "",
        res.removed?.length ? `Reset ${res.removed.join(", ")}` : "",
      ].filter(Boolean);
      setMsg(ok || (parts.length ? parts.join(". ") + "." : "Nothing changed."));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const remove = (key: string) => {
    if (!confirm(`Reset ${key}? The ENV_FILE value (or the default) applies again.`)) return;
    run({ remove: [key] });
  };

  const addKey = () => {
    const key = newKey.trim().toUpperCase();
    const k = key.startsWith("SKOPAQ_") ? key : `SKOPAQ_${key}`;
    const s = byKey[k];
    if (!s) return setError(`${k} is not a SkopaqTrader setting.`);
    if (s.locked) return setError(`${k} cannot be changed here (${s.locked}).`);
    setError(null);
    setFilter(k);
    setEditing(k);
    setNewKey("");
  };

  if (!isAdmin) {
    return (
      <div className="space-y-5">
        <PageTitle title="Environment" />
        <Card><p className="text-sm text-gray-400">Only an admin can see and change the server settings.</p></Card>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageTitle title="Environment" right={<Link href="/settings" className="text-sm text-blue-400">← Settings</Link>} />

      <Card>
        <ul className="text-xs text-gray-400 space-y-1 list-disc pl-4">
          <li>Values saved here <b>override ENV_FILE</b> and survive deploys. <b>Reset</b> brings the ENV_FILE value back.</li>
          <li>They apply to the dashboard at once, to the scheduler before its next session (never a running one), to
            each new daemon/monitor run, and to the Telegram bot after its restart.</li>
          <li>Secret values are never shown. Every change is logged and sent to Telegram.</li>
        </ul>
      </Card>

      <ErrorBox error={env.error || error} />
      {msg && <div className="text-sm text-blue-300">{msg}</div>}

      {env.loading && !env.data ? <Loading /> : env.data && (
        <>
          <ModeCard byKey={byKey} busy={busy} run={run} />

          <Card title={`Overridden from the dashboard (${overridden.length})`}>
            {overridden.length === 0 ? <Empty>None: every value comes from ENV_FILE or the defaults.</Empty> : (
              <div className="flex flex-wrap gap-2">
                {overridden.map((s) => (
                  <button key={s.key} onClick={() => setFilter(s.key)}
                    className="font-mono text-xs border border-yellow-800 bg-yellow-900/10 rounded px-2 py-1">
                    {s.key}
                  </button>
                ))}
              </div>
            )}
          </Card>

          <Card title="Add or change a variable">
            <div className="flex gap-2">
              <input list="env-keys" value={newKey} onChange={(e) => setNewKey(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addKey()}
                placeholder="SKOPAQ_..." className={`${INPUT} font-mono`} />
              <datalist id="env-keys">
                {settings.filter((s) => !s.locked).map((s) => <option key={s.key} value={s.key} />)}
              </datalist>
              <Button variant="ghost" onClick={addKey} disabled={!newKey.trim()}>Edit</Button>
            </div>
          </Card>

          <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="🔍 Search settings"
            className={INPUT} />

          {groups.length === 0 ? <Empty>No setting matches “{filter}”.</Empty> : groups.map(([group, items]) => (
            <Card key={group} title={`${group} (${items.length})`}>
              <div className="divide-y divide-gray-800">
                {items.map((s) => (
                  <Row key={s.key} s={s} busy={busy} editing={editing === s.key} setEditing={setEditing}
                    save={(key, value) => run({ set: { [key]: value } })} remove={remove} />
                ))}
              </div>
            </Card>
          ))}

          <Card title="🕒 Change history">
            {env.data.history.length === 0 ? <Empty>No changes yet.</Empty> : (
              <div className="divide-y divide-gray-800">
                {env.data.history.map((h, i) => (
                  <div key={i} className="py-2 text-sm">
                    <div className="flex justify-between gap-3">
                      <span className="break-all">{h.by.replace(/^dashboard:/, "")}</span>
                      <span className="text-xs text-gray-500 shrink-0">{when(h.at)}</span>
                    </div>
                    {h.live?.length > 0 && <div className="text-xs text-red-400">🔴 LIVE on: {h.live.join(", ")}</div>}
                    {Object.entries(h.set || {}).map(([k, v]) => (
                      <div key={k} className="font-mono text-xs text-gray-400 break-all">{k}={v}</div>
                    ))}
                    {(h.removed || []).map((k) => (
                      <div key={k} className="font-mono text-xs text-gray-500 break-all">reset {k}</div>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </Card>
          <p className="text-xs text-gray-600 break-all">Stored on the server in {env.data.file}</p>
        </>
      )}
    </div>
  );
}
