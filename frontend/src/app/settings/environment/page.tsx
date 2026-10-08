"use client";

import { History, Lock, Plus, Search, SlidersHorizontal } from "lucide-react";
import { useMemo, useState } from "react";

import { useAuth } from "@/components/AuthGate";
import { Badge, Button, Card, Empty, ErrorBox, Loading, Notice, PageTitle } from "@/components/ui";
import AiEndpointCard from "@/components/AiEndpointCard";
import ModeSwitch from "@/components/ModeSwitch";
import { when } from "@/lib/api";
import { saveEnvAsking as send, type EnvData, type Setting } from "@/lib/env";
import { useApi } from "@/lib/hooks";




const GROUP_ORDER = [
  "Trading mode", "Scheduler", "Broker (INDstocks)", "Telegram", "Daemon, scanner & monitor",
  "Risk & sizing", "AI models & keys", "Other",
];

const INPUT = "field";

const SOURCE_LABEL: Record<Setting["source"], string> = {
  dashboard: "DASHBOARD",
  server: "ENV_FILE",
  default: "DEFAULT",
};

function shown(s: Setting): string {
  if (s.secret) return s.is_set ? "•••••••• (set)" : "not set";
  return s.value === "" || s.value === null ? "(empty)" : s.value;
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
          <div className="font-mono text-xs break-all text-gray-100">{s.key}</div>
          {s.help && <div className="text-xs text-gray-500 mt-0.5">{s.help}</div>}
          <div className={`text-sm mt-1 break-all ${s.secret ? "text-gray-400" : ""}`}>{shown(s)}</div>
          {s.locked && <div className="mt-1 flex items-center gap-1 text-xs text-amber-300/80"><Lock className="h-3 w-3" /> {s.locked}: ENV_FILE only</div>}
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <Badge tone={s.source === "dashboard" ? "warning" : s.source === "server" ? "ok" : "neutral"}>
            {SOURCE_LABEL[s.source]}
          </Badge>
          {!s.locked && !editing && (
            <div className="flex gap-1">
              <button onClick={() => setEditing(s.key)} className="link text-xs font-medium">
                Edit
              </button>
              {s.source === "dashboard" && (
                <button onClick={() => remove(s.key)} disabled={busy}
                  className="ml-2 text-xs font-medium text-rose-300 hover:text-rose-200">
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
        <PageTitle title="Environment" icon={SlidersHorizontal} />
        <Notice tone="neutral">Only an admin can see and change the server settings.</Notice>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageTitle title="Environment" icon={SlidersHorizontal} subtitle="Server settings: mode, keys and limits, without a redeploy" />

      <Card>
        <ul className="list-disc space-y-1.5 pl-4 text-xs text-gray-400">
          <li>Values saved here <b>override ENV_FILE</b> and survive deploys. <b>Reset</b> brings the ENV_FILE value back.</li>
          <li>They apply to the dashboard at once, to the scheduler before its next session (never a running one), to
            each new daemon/monitor run, and to the Telegram bot after its restart.</li>
          <li>Secret values are never shown. Every change is logged and sent to Telegram.</li>
        </ul>
      </Card>

      <ErrorBox error={env.error || error} />
      {msg && <Notice tone="info">{msg}</Notice>}

      {env.loading && !env.data ? <Loading /> : env.data && (
        <>
          <ModeSwitch env={env.data} onChange={env.setData} />

          <AiEndpointCard
            configured={["SKOPAQ_CUSTOM_LLM_BASE_URL", "SKOPAQ_CUSTOM_LLM_API_KEY", "SKOPAQ_CUSTOM_LLM_MODEL"].every((k) => byKey[k]?.is_set)}
            onPick={(k) => { setFilter(k); setEditing(k); }} />

          <Card title={`Overridden from the dashboard (${overridden.length})`}>
            {overridden.length === 0 ? <Empty>None: every value comes from ENV_FILE or the defaults.</Empty> : (
              <div className="flex flex-wrap gap-2">
                {overridden.map((s) => (
                  <button key={s.key} onClick={() => setFilter(s.key)}
                    className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-2.5 py-1 font-mono text-xs text-amber-200 transition hover:bg-amber-500/10">
                    {s.key}
                  </button>
                ))}
              </div>
            )}
          </Card>

          <Card title="Add or change a variable" icon={Plus}>
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

          <div className="relative">
            <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Search settings"
              className="field pl-10" />
          </div>

          {groups.length === 0 ? <Empty>No setting matches “{filter}”.</Empty> : groups.map(([group, items]) => (
            <Card key={group} title={`${group} (${items.length})`}>
              <div className="divide-y divide-white/[0.04]">
                {items.map((s) => (
                  <Row key={s.key} s={s} busy={busy} editing={editing === s.key} setEditing={setEditing}
                    save={(key, value) => run({ set: { [key]: value } })} remove={remove} />
                ))}
              </div>
            </Card>
          ))}

          <Card title="Change history" icon={History}>
            {env.data.history.length === 0 ? <Empty>No changes yet.</Empty> : (
              <div className="divide-y divide-white/[0.04]">
                {env.data.history.map((h, i) => (
                  <div key={i} className="py-2 text-sm">
                    <div className="flex justify-between gap-3">
                      <span className="break-all">{h.by.replace(/^dashboard:/, "")}</span>
                      <span className="text-xs text-gray-500 shrink-0">{when(h.at)}</span>
                    </div>
                    {h.live?.length > 0 && <div className="text-xs text-rose-300">LIVE on: {h.live.join(", ")}</div>}
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
