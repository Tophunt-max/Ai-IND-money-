"use client";

import { Layers, Lightbulb, PlugZap, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge, Button, Card, Empty, ErrorBox, Field, KV, Notice, PageTitle, Segmented, Select, StatCard } from "@/components/ui";
import { api, inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Contract {
  tradingsymbol: string;
  strike: number;
  option_type: "CE" | "PE";
  ltp: number;
  bid: number;
  ask: number;
  volume: number;
  oi: number;
  iv: number;
  distance_pct: number;
  premium_yield_pct: number;
  days_to_expiry: number;
  lot_size: number;
}

interface Chain {
  symbol: string;
  spot_price: number;
  expiry: string;
  calls: Contract[];
  puts: Contract[];
  lot_size: number;
  fetched_at: string;
}

interface Suggestion {
  symbol: string;
  strategy: string;
  spot_price: number;
  expiry: string;
  trade: null | {
    strategy: string;
    sell_contract: Contract;
    sell_contract_2: Contract | null;
    premium: number;
    max_profit: number;
    max_loss: number;
    breakeven: number;
    margin_required: number;
    win_probability_pct: number;
    risk_reward: string;
    confidence: number;
    reasoning: string;
  };
}

const UNDERLYINGS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "RELIANCE", "HDFCBANK", "INFY", "TCS"];
const STRATEGIES = [
  { value: "SHORT_PUT", label: "Short put" },
  { value: "SHORT_CALL", label: "Short call" },
  { value: "SHORT_STRANGLE", label: "Short strangle" },
] as const;

const n2 = (v: number | null | undefined, d = 2) => (v == null ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d }));

export default function OptionsPage() {
  const broker = useApi<{ kite: { configured: boolean; connected: boolean } }>("/api/dashboard/broker");
  const [symbol, setSymbol] = useState("NIFTY");
  const [expiry, setExpiry] = useState(0);
  const [strategy, setStrategy] = useState<(typeof STRATEGIES)[number]["value"]>("SHORT_PUT");
  const [chain, setChain] = useState<Chain | null>(null);
  const [idea, setIdea] = useState<Suggestion | null>(null);
  const [busy, setBusy] = useState<"chain" | "idea" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setBusy("chain");
    setError(null);
    try {
      setChain(await api<Chain>(`/api/dashboard/options/chain?symbol=${symbol}&expiry_index=${expiry}`));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const suggest = async () => {
    setBusy("idea");
    setError(null);
    try {
      setIdea(await api<Suggestion>(`/api/dashboard/options/suggest?symbol=${symbol}&strategy=${strategy}&expiry_index=${expiry}`));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  // Rows by strike, around the spot price
  const rows = useMemo(() => {
    if (!chain) return [];
    const strikes = new Map<number, { ce?: Contract; pe?: Contract }>();
    chain.calls.forEach((c) => strikes.set(c.strike, { ...(strikes.get(c.strike) || {}), ce: c }));
    chain.puts.forEach((p) => strikes.set(p.strike, { ...(strikes.get(p.strike) || {}), pe: p }));
    const all = Array.from(strikes.entries()).sort((a, b) => a[0] - b[0]);
    const atm = all.reduce((best, [k], i) => (Math.abs(k - chain.spot_price) < Math.abs(all[best][0] - chain.spot_price) ? i : best), 0);
    return all.slice(Math.max(0, atm - 12), atm + 13);
  }, [chain]);

  const kite = broker.data?.kite;
  const t = idea?.trade;

  return (
    <div className="space-y-6">
      <PageTitle title="Options" icon={Layers} subtitle="Option chain and rule-based option-selling ideas (read only, no orders)" />

      {kite && !kite.connected && (
        <Notice tone="warning" icon={PlugZap} title="Kite is not connected"
          action={<Link href="/broker" className="text-sm font-medium underline">Connect</Link>}>
          The option chain comes from Zerodha Kite. Log in to Kite first.
        </Notice>
      )}

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Underlying" className="w-44">
            <Select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              {UNDERLYINGS.map((s) => <option key={s} value={s}>{s}</option>)}
            </Select>
          </Field>
          <Field label="Expiry" className="w-40">
            <Select value={expiry} onChange={(e) => setExpiry(Number(e.target.value))}>
              {["Nearest", "Next", "3rd", "4th"].map((l, i) => <option key={i} value={i}>{l}</option>)}
            </Select>
          </Field>
          <Button icon={RefreshCw} loading={busy === "chain"} onClick={load}>Load chain</Button>
        </div>
      </Card>

      <ErrorBox error={error} />

      <Card title="Option-selling idea" icon={Lightbulb} subtitle="Picks an OTM strike by distance, premium and yield. You place any order yourself.">
        <div className="flex flex-wrap items-center gap-3">
          <Segmented value={strategy} onChange={setStrategy} options={STRATEGIES} size="sm" />
          <Button variant="ghost" size="sm" icon={Lightbulb} loading={busy === "idea"} onClick={suggest}>Suggest</Button>
        </div>
        {idea && (
          <div className="mt-5">
            {!t ? <Empty icon={Lightbulb}>No suitable {idea.strategy.replace("_", " ").toLowerCase()} for {idea.symbol} right now.</Empty> : (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="info">{t.strategy.replace("_", " ")}</Badge>
                  <span className="font-mono text-sm text-white">{t.sell_contract.tradingsymbol}</span>
                  {t.sell_contract_2 && <span className="font-mono text-sm text-white">+ {t.sell_contract_2.tradingsymbol}</span>}
                </div>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <StatCard title="Premium" value={inr(t.premium)} tone="ok" />
                  <StatCard title="Max loss" value={t.max_loss ? inr(t.max_loss) : "Unlimited"} tone="error" />
                  <StatCard title="Win probability" value={`${n2(t.win_probability_pct, 1)}%`} tone="info" />
                  <StatCard title="Margin (est.)" value={inr(t.margin_required, 0)} />
                </div>
                <KV cols={3} items={[
                  ["Spot", n2(idea.spot_price)],
                  ["Breakeven", n2(t.breakeven)],
                  ["Confidence", `${n2(t.confidence, 0)}%`],
                ]} />
                {t.reasoning && <p className="text-sm text-gray-400">{t.reasoning}</p>}
              </div>
            )}
          </div>
        )}
      </Card>

      {chain && (
        <Card title={`${chain.symbol} option chain`} subtitle={`Spot ${n2(chain.spot_price)} · expiry ${chain.expiry} · lot ${chain.lot_size} · ${when(chain.fetched_at)}`} padded>
          {rows.length === 0 ? <Empty>No contracts for this expiry.</Empty> : (
            <div className="-mx-5 overflow-x-auto">
              <table className="num w-full min-w-[720px] text-xs">
                <thead>
                  <tr className="border-b border-white/[0.06] text-[10px] uppercase tracking-wider text-gray-500">
                    <th className="px-3 py-2 text-right">OI</th><th className="px-3 py-2 text-right">IV</th><th className="px-3 py-2 text-right">Call LTP</th>
                    <th className="px-3 py-2 text-center text-gray-300">Strike</th>
                    <th className="px-3 py-2 text-left">Put LTP</th><th className="px-3 py-2 text-left">IV</th><th className="px-3 py-2 text-left">OI</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.03]">
                  {rows.map(([strike, { ce, pe }]) => {
                    const itmCall = strike < chain.spot_price;
                    return (
                      <tr key={strike} className="hover:bg-white/[0.02]">
                        <td className={`px-3 py-2 text-right ${itmCall ? "bg-emerald-500/[0.04]" : ""}`}>{ce ? n2(ce.oi, 0) : "—"}</td>
                        <td className={`px-3 py-2 text-right ${itmCall ? "bg-emerald-500/[0.04]" : ""}`}>{ce ? n2(ce.iv, 1) : "—"}</td>
                        <td className={`px-3 py-2 text-right font-medium text-emerald-300 ${itmCall ? "bg-emerald-500/[0.04]" : ""}`}>{ce ? n2(ce.ltp) : "—"}</td>
                        <td className="px-3 py-2 text-center font-semibold text-white">{n2(strike, 0)}</td>
                        <td className={`px-3 py-2 font-medium text-rose-300 ${!itmCall ? "bg-rose-500/[0.04]" : ""}`}>{pe ? n2(pe.ltp) : "—"}</td>
                        <td className={`px-3 py-2 ${!itmCall ? "bg-rose-500/[0.04]" : ""}`}>{pe ? n2(pe.iv, 1) : "—"}</td>
                        <td className={`px-3 py-2 ${!itmCall ? "bg-rose-500/[0.04]" : ""}`}>{pe ? n2(pe.oi, 0) : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
