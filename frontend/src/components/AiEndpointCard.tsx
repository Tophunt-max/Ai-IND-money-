"use client";

import { Bot, CheckCircle2, PlugZap } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";

import { Badge, Button, Card, ErrorBox, KV, Notice } from "./ui";

interface Check {
  base_url: string;
  model: string;
  judge_model: string;
  models: string[] | null;
  models_error: string | null;
  ok: boolean;
  reply: string | null;
  error: string | null;
  seconds?: number;
}

/** Custom OpenAI-compatible AI gateway (e.g. CodeCraft API): status and a live test. */
export default function AiEndpointCard({ configured, onPick }: {
  configured: boolean;
  onPick: (key: string) => void;
}) {
  const [res, setRes] = useState<Check | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const test = async () => {
    setBusy(true);
    setError(null);
    try {
      setRes(await api<Check>("/api/dashboard/llm/check", { method: "POST", json: {} }));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="AI gateway (OpenAI-compatible)" icon={Bot}
      subtitle="e.g. CodeCraft API — used by every agent when set, Gemini stays the fallback"
      right={<Badge tone={configured ? "ok" : "neutral"} dot>{configured ? "Configured" : "Not set"}</Badge>}>
      <ol className="mb-4 list-decimal space-y-1 pl-5 text-xs text-gray-400">
        <li>Set <button className="link font-mono" onClick={() => onPick("SKOPAQ_CUSTOM_LLM_BASE_URL")}>SKOPAQ_CUSTOM_LLM_BASE_URL</button> (e.g. https://codecraftapi.com/v1)</li>
        <li>Set <button className="link font-mono" onClick={() => onPick("SKOPAQ_CUSTOM_LLM_API_KEY")}>SKOPAQ_CUSTOM_LLM_API_KEY</button></li>
        <li>Set <button className="link font-mono" onClick={() => onPick("SKOPAQ_CUSTOM_LLM_MODEL")}>SKOPAQ_CUSTOM_LLM_MODEL</button> and optionally{" "}
          <button className="link font-mono" onClick={() => onPick("SKOPAQ_CUSTOM_LLM_JUDGE_MODEL")}>SKOPAQ_CUSTOM_LLM_JUDGE_MODEL</button>, then Test</li>
      </ol>
      <Button icon={PlugZap} loading={busy} onClick={test} disabled={!configured}>Test connection</Button>
      <div className="mt-4 space-y-3">
        <ErrorBox error={error} />
        {res && (
          <>
            {res.ok
              ? <Notice tone="ok" icon={CheckCircle2} title={`Model answered in ${res.seconds ?? "?"}s`}>Reply: “{res.reply}”</Notice>
              : <Notice tone="error" title="The model did not answer">{res.error}</Notice>}
            <KV items={[["Endpoint", res.base_url], ["Model", res.model], ["Judge model", res.judge_model]]} />
            {res.models && res.models.length > 0 && (
              <div>
                <div className="label">Models this key can use ({res.models.length})</div>
                <div className="flex max-h-48 flex-wrap gap-1.5 overflow-y-auto">
                  {res.models.map((m) => (
                    <span key={m} className={`rounded-md px-2 py-0.5 font-mono text-[11px] ring-1 ${m === res.model || m === res.judge_model ? "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30" : "bg-white/[0.03] text-gray-400 ring-white/[0.06]"}`}>{m}</span>
                  ))}
                </div>
              </div>
            )}
            {res.models_error && <p className="text-xs text-gray-500">Model list: {res.models_error}</p>}
          </>
        )}
      </div>
    </Card>
  );
}
