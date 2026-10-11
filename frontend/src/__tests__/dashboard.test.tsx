import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/supabase", () => ({ accessToken: async () => "tok" }));
vi.mock("@/components/AuthGate", () => ({ useAuth: () => ({ isAdmin: true, user: null }) }));

import CandleChart from "@/components/CandleChart";
import { BarChart, drawdowns, EquityChart } from "@/components/charts";
import { chainStats } from "@/lib/options";
import OfflineBanner from "@/components/OfflineBanner";
import RiskCapacity from "@/components/RiskCapacity";
import RLAgents, { rlTradingMode, type RLStatus } from "@/components/RLAgents";
import StrategyBuilder, { EMPTY, specProblems } from "@/components/StrategyBuilder";
import SwingModel, { swingTradingMode, type SwingStatus } from "@/components/SwingModel";
import { api, ApiError } from "@/lib/api";

class RO { observe() {} disconnect() {} }
(globalThis as any).ResizeObserver = RO;

function respond(map: Record<string, unknown>, status = 200) {
  globalThis.fetch = vi.fn(async (url: any) => {
    const key = Object.keys(map).find((k) => String(url).includes(k));
    const body = key ? map[key] : { detail: "Not Found" };
    return new Response(JSON.stringify(body), { status: key ? status : 404 });
  }) as any;
}

afterEach(() => cleanup());

describe("api()", () => {
  it("sends the bearer token and raises ApiError with the detail", async () => {
    respond({ "/x": { detail: "nope" } }, 409);
    await expect(api("/x")).rejects.toMatchObject({ status: 409, message: "nope" });
    const call = (globalThis.fetch as any).mock.calls[0];
    expect(call[1].headers.Authorization).toBe("Bearer tok");
  });

  it("announces a 401 so the app signs out, and explains a missing route", async () => {
    const seen = vi.fn();
    window.addEventListener("aiind:unauthorized", seen);
    respond({ "/me": { detail: "expired" } }, 401);
    await expect(api("/me")).rejects.toBeInstanceOf(ApiError);
    expect(seen).toHaveBeenCalled();
    respond({});
    await expect(api("/new-feature")).rejects.toThrow(/backend update/);
  });

  it("passes an Idempotency-Key header through", async () => {
    respond({ "/control/orders": { ok: true } });
    await api("/api/dashboard/control/orders", { method: "POST", json: {}, headers: { "Idempotency-Key": "k1" } });
    expect((globalThis.fetch as any).mock.calls[0][1].headers["Idempotency-Key"]).toBe("k1");
  });
});

describe("CandleChart", () => {
  const candles = Array.from({ length: 30 }, (_, i) => ({ t: 1000 + i * 60, o: 100 + i, h: 102 + i, l: 99 + i, c: 101 + i, v: 10 }));

  it("draws EMA overlays, pattern markers and signal levels", () => {
    const { container } = render(
      <CandleChart candles={candles}
        overlays={candles.map((c, i) => ({ t: c.t, ema9: i > 8 ? c.c : null, ema21: i > 20 ? c.c - 1 : null }))}
        markers={[{ t: candles[10].t, bias: "bullish", names: ["hammer"] }, { t: 1, bias: "bearish", names: ["x"] }]}
        levels={[{ label: "Entry", price: 120, tone: "entry" }, { label: "Stop", price: 118, tone: "stop" }]} />,
    );
    expect(container.querySelectorAll("polyline")).toHaveLength(2);
    expect(container.querySelectorAll("polygon")).toHaveLength(1);   // the unknown time is skipped
    expect(screen.getByText(/^Entry/)).toBeTruthy();
    expect(screen.getByText(/^Stop/)).toBeTruthy();
  });

  it("says when there is not enough data", () => {
    render(<CandleChart candles={candles.slice(0, 1)} />);
    expect(screen.getByText(/Not enough data/)).toBeTruthy();
  });
});

describe("RiskCapacity", () => {
  beforeEach(() => vi.useRealTimers());

  it("shows entries blocked when a placement is unreconciled", async () => {
    respond({
      "/api/dashboard/risk": { kill_switch: { halted: false }, ceilings: { max_daily_loss_pct: 0.03 },
        working: { max_consecutive_losses: 4 }, realized: { day: -1500, loss_streak: 1 }, realized_source: "supabase" },
      "/api/dashboard/control/reconcile": { blocking_entries: true, blockers: [{}] },
    });
    render(<RiskCapacity portfolioValue={100000} />);
    await waitFor(() => expect(screen.getByText("Entries blocked")).toBeTruthy());
    expect(screen.getByText("1 unreconciled")).toBeTruthy();
    expect(screen.getByText(/of ₹3,000/)).toBeTruthy();
  });

  it("shows entries allowed when nothing blocks", async () => {
    respond({
      "/api/dashboard/risk": { kill_switch: { halted: false }, ceilings: { max_daily_loss_pct: 0.03 },
        working: { max_consecutive_losses: 4 }, realized: { day: 200, loss_streak: 0 }, realized_source: "supabase" },
      "/api/dashboard/control/reconcile": { blocking_entries: false, blockers: [] },
    });
    render(<RiskCapacity portfolioValue={100000} />);
    await waitFor(() => expect(screen.getByText("Entries allowed")).toBeTruthy());
  });
});

describe("OfflineBanner", () => {
  it("appears while offline and goes away on reconnect", async () => {
    const online = vi.spyOn(navigator, "onLine", "get");
    online.mockReturnValue(true);
    render(<OfflineBanner />);
    expect(screen.queryByRole("status")).toBeNull();
    online.mockReturnValue(false);
    await act(async () => { window.dispatchEvent(new Event("offline")); });
    expect(screen.getByRole("status").textContent).toMatch(/Offline/);
    online.mockReturnValue(true);
    await act(async () => { window.dispatchEvent(new Event("online")); });
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("RLAgents", () => {
  const base: RLStatus = {
    enabled: true, in_scalp_strategies: true, allow_live: false, paper_use_shadow: true, torch: true,
    algos: ["ppo", "a2c", "dqn"], algo: "ppo", paper_policy: "rl-ppo-1", live_policy: null,
    champion: null, shadow: "rl-ppo-1", shadow_days: 7,
    versions: [{ version: "rl-ppo-1", state: "shadow", algo: "ppo", train_sessions: 40, test_sessions: 18,
      model: { trades: 52, net: 1840, profit_factor: 1.3, max_drawdown: 900, charges: 2100 },
      baseline: { trades: 18, net: 400, profit_factor: 1.1 }, exits: { target: 20, stop: 25, agent: 7 }, gate_failures: [] }],
  };

  it("trades a shadow policy on paper only, never live without the switch and a champion", () => {
    expect(rlTradingMode(base)).toEqual({ paper: "on · shadow", live: "off (SKOPAQ_RL_ALLOW_LIVE)" });
    expect(rlTradingMode({ ...base, allow_live: true }).live).toBe("needs an approved champion");
    expect(rlTradingMode({ ...base, allow_live: true, champion: "rl-ppo-1", live_policy: "rl-ppo-1", paper_policy: "rl-ppo-1" }))
      .toEqual({ paper: "on · champion", live: "on · champion" });
    expect(rlTradingMode({ ...base, in_scalp_strategies: false })).toEqual({ paper: "off", live: "off" });
  });

  it("lists the policies with their unseen-day results and a promote button for shadow", async () => {
    respond({ "/learning/rl": base });
    render(<RLAgents />);
    await waitFor(() => expect(screen.getAllByText("rl-ppo-1").length).toBeGreaterThan(0));
    expect(screen.getByText("Promote")).toBeTruthy();
    expect(screen.getByText(/target 20, stop 25, agent 7/)).toBeTruthy();
  });
});

describe("SwingModel", () => {
  const base: SwingStatus = {
    mode: "paper", champion: null, shadow: "swing-lightgbm-1", active: "swing-lightgbm-1", rules_only: false, shadow_days: 7,
    config: { symbols: "NIFTY 50", model_kind: "lightgbm", target_rr: 2, train_days: 1825, max_trades: 3, min_net_rr: 1 },
    versions: [{ version: "swing-lightgbm-1", state: "shadow", model_kind: "lightgbm", setups: 900, threshold: 0.55,
      model: { trades: 60, net: 21500, profit_factor: 1.4, win_rate: 0.52 }, baseline: { trades: 180, net: 4200, profit_factor: 1.05 },
      by_setup: { breakout: { setups: 80, taken: 30, net_all: 900, net_taken: 9000 } }, gate_failures: [] }],
  };

  it("trades a shadow on paper only; live needs an approved champion", () => {
    expect(swingTradingMode(base)).toEqual({ paper: "shadow", live: "off — needs an approved champion" });
    expect(swingTradingMode({ ...base, champion: "swing-lightgbm-1" })).toEqual({ paper: "champion", live: "champion" });
    expect(swingTradingMode({ ...base, shadow: null, active: null }).paper).toBe("off — no model");
    expect(swingTradingMode({ ...base, shadow: null, active: null, rules_only: true }).paper).toBe("rules only (no model)");
  });

  it("lists the models with their unseen-date results and a promote button for a shadow", async () => {
    respond({ "/dashboard/swing": base });
    render(<SwingModel />);
    await waitFor(() => expect(screen.getAllByText("swing-lightgbm-1").length).toBeGreaterThan(0));
    expect(screen.getByText("Promote")).toBeTruthy();
    expect(screen.getByText("Train swing model")).toBeTruthy();
    expect(screen.getByText(/breakout 30\/80/)).toBeTruthy();
  });
});

describe("StrategyBuilder", () => {
  const task = (t: string) => (t === "win" ? "classifier" : "regressor");
  const catalog = {
    features: { rsi: { kind: "period", about: "RSI" }, ema_gap: { kind: "basic", about: "EMA gap" } },
    targets: { win: { task: "classifier", about: "win" }, forward_return: { task: "regressor", about: "fwd" } },
    entry_types: ["auto", "probability", "zscore", "value"], timeframes: [60, 300, 900],
  };

  it("flags what the server would refuse", () => {
    expect(specProblems({ ...EMPTY, name: "my_mom" }, task)).toEqual([]);
    expect(specProblems({ ...EMPTY, name: "X" }, task)[0]).toMatch(/name/);
    expect(specProblems({ ...EMPTY, name: "abc", timeframes: [300] }, task)).toContain("the first timeframe must be 1m");
    expect(specProblems({ ...EMPTY, name: "abc", targets: [{ type: "win", horizon: 5 }, { type: "forward_return", horizon: 5 }] }, task)[0]).toMatch(/targets/);
    expect(specProblems({ ...EMPTY, name: "abc", entry: { type: "zscore", min: 1 } }, task)[0]).toMatch(/regression/);
  });

  it("lists saved strategies, the one in use, and saves a new one", async () => {
    respond({ "/learning/strategies": {
      in_use: { kind: "spec", name: "my_mom" }, env_strategy: "", continual: { adaptive: true, rl: false }, catalog,
      specs: [{ spec: { ...EMPTY, name: "my_mom", features: ["rsi"] }, saved_by: "a@x", saved_at: "2026-10-11T10:00:00Z", revision: 2 }],
    } });
    render(<StrategyBuilder />);
    await waitFor(() => expect(screen.getAllByText("my_mom").length).toBeGreaterThan(0));
    expect(screen.getByText("in use")).toBeTruthy();
    expect(screen.getByText("adaptive on")).toBeTruthy();
    expect(screen.getByText("training: my_mom")).toBeTruthy();
  });
});

describe("charts", () => {
  it("computes running drawdowns", () => {
    const d = drawdowns([0, 100, 50, 150, 120], 1000);
    expect(d.abs).toEqual([0, 0, -50, 0, -30]);
    expect(d.max).toBe(-50);
    expect(d.maxPct).toBeCloseTo(-50 / 1100);
  });

  it("draws one bar per value and an equity line with its drawdown pane", () => {
    const { container } = render(<BarChart bars={[{ label: "a", v: 5 }, { label: "b", v: -3 }, { label: "c", v: 0 }]} />);
    expect(container.querySelectorAll("rect[data-bar]")).toHaveLength(3);
    cleanup();
    render(<EquityChart points={[{ label: "d1", v: 0 }, { label: "d2", v: 100 }, { label: "d3", v: 40 }]} baseline={0} />);
    expect(screen.getByLabelText("equity chart")).toBeTruthy();
    expect(screen.getAllByText(/DD/).length).toBeGreaterThan(0);
  });

  it("zooms a long candle history to a readable window", () => {
    const many = Array.from({ length: 400 }, (_, i) => ({ t: 1000 + i * 60, o: 100, h: 101, l: 99, c: 100.5, v: 1 }));
    render(<CandleChart candles={many} />);
    expect(screen.getByText(/\/400$/)).toBeTruthy();
    expect(screen.getByLabelText("Zoom in")).toBeTruthy();
  });
});

describe("option chain stats", () => {
  it("gives the put-call ratio and the max-pain strike", () => {
    const calls = [{ strike: 100, oi: 10 }, { strike: 110, oi: 50 }, { strike: 120, oi: 80 }];
    const puts = [{ strike: 100, oi: 70 }, { strike: 110, oi: 40 }, { strike: 120, oi: 5 }];
    const s = chainStats(calls, puts);
    expect(s.pcr).toBeCloseTo(115 / 140);
    expect(s.maxPain).toBe(110);
  });
});
