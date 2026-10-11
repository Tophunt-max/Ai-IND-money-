"use client";

import { Calculator, Layers, Lightbulb, PlugZap, Plus, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { IvSmile, PayoffChart } from "@/components/OptionCharts";
import { Badge, Button, Card, cx, Empty, ErrorBox, Field, KV, Notice, PageTitle, Segmented, Select, StatCard, Tabs } from "@/components/ui";
import { api, inr, when } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { chainStats } from "@/lib/options";
import { summarize, type Leg } from "@/lib/payoff";

interface Contract {
  tradingsymbol: string; security_id: string; strike: number; option_type: "CE" | "PE";
  ltp: number; bid: number; ask: number; volume: number; oi: number; iv: number; delta: number; theta: number;
  oi_change: number; distance_pct: number; premium_yield_pct: number; days_to_expiry: number; lot_size: number;
}

interface Chain {
  symbol: string; spot_price: number; expiry: string; calls: Contract[]; puts: Contract[];
  lot_size: number; expiries: string[]; fetched_at: string;
}

interface Suggestion {
  symbol: string; strategy: string; spot_price: number; expiry: string;
  trade: null | {
    strategy: string; sell_contract: Contract; sell_contract_2: Contract | null; premium: number; max_profit: number;
    max_loss: number; breakeven: number; margin_required: number; win_probability_pct: number; risk_reward: string;
    confidence: number; reasoning: string;
  };
}

const UNDERLYINGS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "RELIANCE", "HDFCBANK", "INFY", "TCS"];
const STRATEGIES = [
  { value: "SHORT_PUT", label: "Short put" }, { value: "SHORT_CALL", label: "Short call" }, { value: "SHORT_STRANGLE", label: "Short strangle" },
] as const;

const n2 = (v: number | null | undefined, d = 2) => (v == null ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d }));
let legSeq = 0;
const newLeg = (c: Contract, side: "BUY" | "SELL"): Leg => ({ id: `l${++legSeq}`, type: c.option_type, side, strike: c.strike, premium: c.ltp, lots: 1 });

function OiChart({ rows, spot, maxPain, mode }: { rows: [number, { ce?: Contract; pe?: Contract }][]; spot: number; maxPain: number | null; mode: "oi" | "chg" }) {
  const v = (c?: Contract) => (mode === "oi" ? c?.oi || 0 : c?.oi_change || 0);
  const max = Math.max(1, ...rows.flatMap(([, r]) => [Math.abs(v(r.ce)), Math.abs(v(r.pe))]));
  const atmStrike = rows.reduce((b, [k]) => (Math.abs(k - spot) < Math.abs(b - spot) ? k : b), rows[0]?.[0] ?? 0);
  return (
    <div className="space-y-0.5">
      <div className="mb-2 grid grid-cols-[1fr_72px_1fr] text-[11px] font-medium text-gray-500"><span className="text-right">Calls (resistance)</span><span /><span>Puts (support)</span></div>
      {rows.map(([strike, { ce, pe }]) => (
        <div key={strike} className={cx("grid grid-cols-[1fr_72px_1fr] items-center gap-2 rounded-lg px-1 py-0.5", strike === atmStrike && "bg-brand-500/10")}>
          <div className="flex h-4 justify-end"><div className={cx("h-full rounded-l-md", v(ce) >= 0 ? "bg-rose-500/75" : "bg-rose-500/30")} style={{ width: `${(Math.abs(v(ce)) / max) * 100}%` }} title={`Call ${mode === "oi" ? "OI" : "OI change"} ${n2(v(ce), 0)}`} /></div>
          <div className="num text-center text-[11px] font-semibold text-gray-200">
            {n2(strike, 0)}{strike === maxPain && <span className="ml-1 rounded bg-amber-500/20 px-1 text-[9px] text-amber-300" title="max pain">MP</span>}
          </div>
          <div className="flex h-4"><div className={cx("h-full rounded-r-md", v(pe) >= 0 ? "bg-emerald-500/75" : "bg-emerald-500/30")} style={{ width: `${(Math.abs(v(pe)) / max) * 100}%` }} title={`Put ${mode === "oi" ? "OI" : "OI change"} ${n2(v(pe), 0)}`} /></div>
        </div>
      ))}
    </div>
  );
}

export default function OptionsPage() {
  const broker = useApi<{ indstocks: { valid: boolean } }>("/api/dashboard/broker");
  const [symbol, setSymbol] = useState("NIFTY");
  const expiries = useApi<{ expiries: string[] }>(broker.data?.indstocks.valid ? `/api/dashboard/options/expiries?symbol=${symbol}` : null, 600000);
  const [expiry, setExpiry] = useState(0);
  const [strategy, setStrategy] = useState<(typeof STRATEGIES)[number]["value"]>("SHORT_PUT");
  const [chain, setChain] = useState<Chain | null>(null);
  const [idea, setIdea] = useState<Suggestion | null>(null);
  const [busy, setBusy] = useState<"chain" | "idea" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"chain" | "oi" | "iv" | "builder">("chain");
  const [oiMode, setOiMode] = useState<"oi" | "chg">("oi");
  const [legs, setLegs] = useState<Leg[]>([]);

  const load = async () => {
    setBusy("chain");
    setError(null);
    try {
      setChain(await api<Chain>(`/api/dashboard/options/chain?symbol=${symbol}&expiry_index=${expiry}`));
      setLegs([]);
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

  const rows = useMemo(() => {
    if (!chain) return [] as [number, { ce?: Contract; pe?: Contract }][];
    const strikes = new Map<number, { ce?: Contract; pe?: Contract }>();
    chain.calls.forEach((c) => strikes.set(c.strike, { ...(strikes.get(c.strike) || {}), ce: c }));
    chain.puts.forEach((p) => strikes.set(p.strike, { ...(strikes.get(p.strike) || {}), pe: p }));
    const all = Array.from(strikes.entries()).sort((a, b) => a[0] - b[0]);
    const atm = all.reduce((best, [k], i) => (Math.abs(k - chain.spot_price) < Math.abs(all[best][0] - chain.spot_price) ? i : best), 0);
    return all.slice(Math.max(0, atm - 12), atm + 13);
  }, [chain]);
  const stats = useMemo(() => (chain ? chainStats(chain.calls, chain.puts) : null), [chain]);
  const atmIv = useMemo(() => {
    if (!chain || !rows.length) return null;
    const [, r] = rows.reduce((b, x) => (Math.abs(x[0] - chain.spot_price) < Math.abs(b[0] - chain.spot_price) ? x : b));
    const ivs = [r.ce?.iv, r.pe?.iv].filter((v): v is number => v != null && v > 0);
    return ivs.length ? ivs.reduce((a, b) => a + b, 0) / ivs.length : null;
  }, [chain, rows]);
  const payoff = useMemo(() => (chain && legs.length ? summarize(legs, chain.spot_price, chain.lot_size || 1) : null), [legs, chain]);
  const token = broker.data?.indstocks;
  const t = idea?.trade;
  const addLeg = (c: Contract | undefined, side: "BUY" | "SELL") => {
    if (!c) return;
    setLegs((x) => [...x, newLeg(c, side)].slice(0, 8));
    setTab("builder");
  };
  const useIdea = () => {
    if (!t) return;
    setLegs([newLeg(t.sell_contract, "SELL"), ...(t.sell_contract_2 ? [newLeg(t.sell_contract_2, "SELL")] : [])]);
    setTab("builder");
  };

  return (
    <div className="space-y-4">
      <PageTitle title="Options" icon={Layers} subtitle="INDstocks option chain, open interest, IV smile and a payoff calculator. Read only: no orders from here." />
      {token && !token.valid && (
        <Notice tone="warning" icon={PlugZap} title="No INDstocks token" action={<Link href="/broker" className="text-sm font-semibold underline">Set token</Link>}>
          The option chain comes from INDstocks. Set today&apos;s token on the Broker page first.
        </Notice>
      )}

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Underlying" className="w-44">
            <Select value={symbol} onChange={(e) => setSymbol(e.target.value)}>{UNDERLYINGS.map((s) => <option key={s} value={s}>{s}</option>)}</Select>
          </Field>
          <Field label="Expiry" className="w-44">
            <Select value={expiry} onChange={(e) => setExpiry(Number(e.target.value))}>
              {(expiries.data?.expiries?.length ? expiries.data.expiries.slice(0, 13)
                : chain?.symbol === symbol && chain.expiries?.length ? chain.expiries.slice(0, 8) : ["Nearest", "Next", "3rd", "4th"])
                .map((l, i) => <option key={i} value={i}>{l}</option>)}
            </Select>
          </Field>
          <Button icon={RefreshCw} loading={busy === "chain"} onClick={load}>Load chain</Button>
        </div>
      </Card>
      <ErrorBox error={error} />

      {chain && stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard title="Spot" value={n2(chain.spot_price)} tone="info" detail={`${chain.expiry} · lot ${chain.lot_size}`} />
          <StatCard title="PCR (OI)" value={stats.pcr == null ? "—" : stats.pcr.toFixed(2)}
            tone={stats.pcr == null ? "neutral" : stats.pcr > 1 ? "ok" : stats.pcr < 0.7 ? "error" : "warning"}
            detail={stats.pcr == null ? undefined : stats.pcr > 1 ? "more put writing (support)" : "more call writing (resistance)"} />
          <StatCard title="Max pain" value={stats.maxPain == null ? "—" : n2(stats.maxPain, 0)}
            detail={stats.maxPain != null ? `${(((stats.maxPain - chain.spot_price) / chain.spot_price) * 100).toFixed(2)}% from spot` : undefined} />
          <StatCard title="ATM IV" value={atmIv == null ? "—" : `${n2(atmIv, 1)}%`} detail={`OI calls ${n2(stats.callOi, 0)} · puts ${n2(stats.putOi, 0)}`} />
        </div>
      )}

      {chain && (
        <section className="surface p-4 sm:p-5">
          <Tabs value={tab} onChange={setTab} className="mb-4" options={[
            { value: "chain", label: "Chain" }, { value: "oi", label: "Open interest" }, { value: "iv", label: "IV smile" },
            { value: "builder", label: "Payoff", count: legs.length || undefined },
          ]} />
          {tab === "chain" && (rows.length === 0 ? <Empty>No contracts for this expiry.</Empty> : (
            <>
              <p className="mb-2 text-[11px] text-gray-500">Tap a call or put price to add it to the payoff calculator (B buy · S sell). {when(chain.fetched_at)}</p>
              <div className="-mx-4 overflow-x-auto sm:-mx-5">
                <table className="num w-full min-w-[860px] text-xs">
                  <thead>
                    <tr className="border-b border-white/[0.06] text-[11px] text-gray-500">
                      <th className="px-3 py-2 text-right">OI</th><th className="px-3 py-2 text-right">IV</th><th className="px-3 py-2 text-right">Delta</th><th className="px-3 py-2 text-right">Call LTP</th>
                      <th className="px-3 py-2 text-center text-gray-300">Strike</th>
                      <th className="px-3 py-2 text-left">Put LTP</th><th className="px-3 py-2 text-left">Delta</th><th className="px-3 py-2 text-left">IV</th><th className="px-3 py-2 text-left">OI</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.04]">
                    {rows.map(([strike, { ce, pe }]) => {
                      const itmCall = strike < chain.spot_price;
                      const cell = (on: boolean) => (on ? "bg-amber-500/[0.06]" : "");
                      return (
                        <tr key={strike} className="hover:bg-white/[0.03]">
                          <td className={`px-3 py-2 text-right ${cell(itmCall)}`}>{ce ? n2(ce.oi, 0) : "—"}</td>
                          <td className={`px-3 py-2 text-right ${cell(itmCall)}`}>{ce ? n2(ce.iv, 1) : "—"}</td>
                          <td className={`px-3 py-2 text-right ${cell(itmCall)}`}>{ce ? n2(ce.delta, 2) : "—"}</td>
                          <td className={`px-3 py-2 text-right ${cell(itmCall)}`}>
                            {ce ? <span className="inline-flex items-center gap-1 font-semibold text-gray-100">{n2(ce.ltp)}
                              <button type="button" onClick={() => addLeg(ce, "BUY")} className="rounded bg-emerald-500/15 px-1 text-[10px] font-bold text-emerald-400">B</button>
                              <button type="button" onClick={() => addLeg(ce, "SELL")} className="rounded bg-rose-500/15 px-1 text-[10px] font-bold text-rose-400">S</button></span> : "—"}
                          </td>
                          <td className="px-3 py-2 text-center font-bold text-white">{n2(strike, 0)}</td>
                          <td className={`px-3 py-2 ${cell(!itmCall)}`}>
                            {pe ? <span className="inline-flex items-center gap-1 font-semibold text-gray-100">{n2(pe.ltp)}
                              <button type="button" onClick={() => addLeg(pe, "BUY")} className="rounded bg-emerald-500/15 px-1 text-[10px] font-bold text-emerald-400">B</button>
                              <button type="button" onClick={() => addLeg(pe, "SELL")} className="rounded bg-rose-500/15 px-1 text-[10px] font-bold text-rose-400">S</button></span> : "—"}
                          </td>
                          <td className={`px-3 py-2 ${cell(!itmCall)}`}>{pe ? n2(pe.delta, 2) : "—"}</td>
                          <td className={`px-3 py-2 ${cell(!itmCall)}`}>{pe ? n2(pe.iv, 1) : "—"}</td>
                          <td className={`px-3 py-2 ${cell(!itmCall)}`}>{pe ? n2(pe.oi, 0) : "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          ))}
          {tab === "oi" && stats && (
            <>
              <div className="mb-3"><Segmented size="sm" value={oiMode} onChange={setOiMode} options={[{ value: "oi", label: "Open interest" }, { value: "chg", label: "OI change" }]} /></div>
              <OiChart rows={rows} spot={chain.spot_price} maxPain={stats.maxPain} mode={oiMode} />
            </>
          )}
          {tab === "iv" && <IvSmile calls={chain.calls} puts={chain.puts} spot={chain.spot_price} height={260} />}
          {tab === "builder" && (
            <div className="space-y-4">
              {legs.length === 0 ? (
                <Empty icon={Calculator} title="No legs yet">Tap B / S next to a price in the Chain tab, or use the option-selling idea below.</Empty>
              ) : (
                <>
                  <ul className="space-y-2">
                    {legs.map((l) => (
                      <li key={l.id} className="flex flex-wrap items-center gap-2 rounded-2xl bg-ink-850 p-2.5 text-sm">
                        <button type="button" onClick={() => setLegs((x) => x.map((y) => (y.id === l.id ? { ...y, side: y.side === "BUY" ? "SELL" : "BUY" } : y)))}>
                          <Badge tone={l.side === "BUY" ? "ok" : "error"}>{l.side}</Badge>
                        </button>
                        <span className="num font-semibold text-white">{n2(l.strike, 0)} {l.type}</span>
                        <label className="num ml-auto flex items-center gap-1 text-xs text-gray-500">₹
                          <input value={l.premium} inputMode="decimal" onChange={(e) => setLegs((x) => x.map((y) => (y.id === l.id ? { ...y, premium: Number(e.target.value) || 0 } : y)))}
                            className="field h-8 w-20 px-2 text-xs" />
                        </label>
                        <label className="num flex items-center gap-1 text-xs text-gray-500">lots
                          <input value={l.lots} inputMode="numeric" onChange={(e) => setLegs((x) => x.map((y) => (y.id === l.id ? { ...y, lots: Math.max(1, Math.floor(Number(e.target.value) || 1)) } : y)))}
                            className="field h-8 w-14 px-2 text-xs" />
                        </label>
                        <button type="button" aria-label="Remove leg" onClick={() => setLegs((x) => x.filter((y) => y.id !== l.id))} className="rounded-lg p-1.5 text-gray-500 hover:text-rose-400"><Trash2 className="h-4 w-4" /></button>
                      </li>
                    ))}
                  </ul>
                  {payoff && (
                    <>
                      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                        <StatCard title="Max profit" value={<span className="text-emerald-400">{payoff.maxProfit == null ? "Unlimited" : inr(payoff.maxProfit, 0)}</span>} tone="ok" />
                        <StatCard title="Max loss" value={<span className="text-rose-400">{payoff.maxLoss == null ? "Unlimited" : inr(payoff.maxLoss, 0)}</span>} tone="error" />
                        <StatCard title="Break-even" value={payoff.breakevens.length ? payoff.breakevens.map((b) => b.toFixed(0)).join(" · ") : "—"} />
                        <StatCard title={payoff.netPremium >= 0 ? "Premium received" : "Premium paid"} value={inr(Math.abs(payoff.netPremium), 0)} detail={`lot ${chain.lot_size}`} />
                      </div>
                      <PayoffChart s={payoff} spot={chain.spot_price} />
                    </>
                  )}
                  <p className="text-[11px] text-gray-500">At expiry, before charges and margin. A calculator only: nothing is placed.</p>
                </>
              )}
              {legs.length > 0 && <Button variant="ghost" size="sm" icon={Trash2} onClick={() => setLegs([])}>Clear</Button>}
            </div>
          )}
        </section>
      )}

      <Card title="Option-selling idea" icon={Lightbulb} subtitle="A rule-based pick of an OTM strike by distance, premium and yield. You place any order yourself.">
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
                  {chain && <Button size="sm" variant="soft" icon={Plus} onClick={useIdea}>Show payoff</Button>}
                </div>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <StatCard title="Premium" value={inr(t.premium)} tone="ok" />
                  <StatCard title="Max loss" value={t.max_loss ? inr(t.max_loss) : "Unlimited"} tone="error" />
                  <StatCard title="Win probability" value={`${n2(t.win_probability_pct, 1)}%`} tone="info" />
                  <StatCard title="Margin (est.)" value={inr(t.margin_required, 0)} />
                </div>
                <KV cols={3} items={[["Spot", n2(idea.spot_price)], ["Breakeven", n2(t.breakeven)], ["Confidence", `${n2(t.confidence, 0)}%`]]} />
                {t.reasoning && <p className="text-sm text-gray-400">{t.reasoning}</p>}
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
