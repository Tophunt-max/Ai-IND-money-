import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/supabase", () => ({ accessToken: async () => "tok" }));
vi.mock("@/components/AuthGate", () => ({ useAuth: () => ({ isAdmin: true, user: { email: "a@b.c", role: "admin" } }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => "/" }));

import { alertDistance } from "@/components/AlertSheet";
import { rankCommands, type Command } from "@/components/CommandPalette";
import { ticketMath } from "@/components/OrderTicket";
import { buildCalendar } from "@/components/PnlCalendar";
import { withLivePrice } from "@/components/PriceChart";
import { heatColor, sectorName } from "@/components/MarketWidgets";
import { ToastProvider, useToast } from "@/components/Toast";
import { Gauge, Sheet, Tabs } from "@/components/ui";
import { bollinger, ema, macd, rsi, sma, vwap } from "@/lib/indicators";
import { parseFrames } from "@/lib/live";
import { summarize, payoff, type Leg } from "@/lib/payoff";
import { activeList, addList, DEFAULT_PREFS, moveInList, readPrefs, resetPrefs, setPrefs, toggleInList, THEME_BOOT } from "@/lib/prefs";

afterEach(() => cleanup());

describe("indicators", () => {
  const xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

  it("computes SMA and EMA with nulls until warmed up", () => {
    expect(sma(xs, 3)).toEqual([null, null, 2, 3, 4, 5, 6, 7, 8, 9]);
    const e = ema(xs, 3);
    expect(e.slice(0, 2)).toEqual([null, null]);
    expect(e[2]).toBe(2);
    expect(e[3]).toBeCloseTo(3);
  });

  it("Bollinger bands are symmetric around the SMA", () => {
    const b = bollinger(xs, 5, 2);
    expect(b.mid[4]).toBe(3);
    expect((b.upper[4] as number) - 3).toBeCloseTo(3 - (b.lower[4] as number));
  });

  it("RSI is 100 for a rising series and bounded", () => {
    expect(rsi(xs, 5)[9]).toBe(100);
    const zig = Array.from({ length: 40 }, (_, i) => 100 + (i % 2 ? 1 : -1));
    const r = rsi(zig, 14).filter((v): v is number => v != null);
    expect(r.every((v) => v >= 0 && v <= 100)).toBe(true);
  });

  it("MACD histogram is line minus signal", () => {
    const s = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i / 4) * 5);
    const m = macd(s);
    const i = 50;
    expect(m.hist[i]).toBeCloseTo((m.line[i] as number) - (m.signal[i] as number));
    expect(m.signal[20]).toBeNull();
  });

  it("VWAP resets each IST day", () => {
    const day1 = 1_700_000_000 - (1_700_000_000 % 86400) + 4 * 3600; // 09:30 IST-ish
    const k = (t: number, p: number, v: number) => ({ t, o: p, h: p, l: p, c: p, v });
    const out = vwap([k(day1, 100, 10), k(day1 + 60, 110, 10), k(day1 + 86400, 50, 5)]);
    expect(out[1]).toBeCloseTo(105);
    expect(out[2]).toBe(50);
  });
});

describe("option payoff", () => {
  const sp: Leg = { id: "a", type: "PE", side: "SELL", strike: 100, premium: 5, lots: 1 };

  it("short put: limited profit, loss below the strike, break-even at strike − premium", () => {
    expect(payoff([sp], 120, 50)).toBe(250);
    expect(payoff([sp], 90, 50)).toBe(-250);
    const s = summarize([sp], 100, 50, 0.3);
    expect(s.maxProfit).toBe(250);
    expect(s.breakevens[0]).toBeCloseTo(95, 0);
    expect(s.netPremium).toBe(250);
  });

  it("long call profit and short call loss are unlimited", () => {
    const lc: Leg = { id: "b", type: "CE", side: "BUY", strike: 100, premium: 3, lots: 2 };
    expect(summarize([lc], 100, 10).maxProfit).toBeNull();
    expect(summarize([{ ...lc, side: "SELL" }], 100, 10).maxLoss).toBeNull();
  });
});

describe("preferences", () => {
  beforeEach(() => { localStorage.clear(); resetPrefs(); });

  it("defaults, saves and sanitises", () => {
    expect(readPrefs().theme).toBe("system");
    setPrefs({ theme: "dark", density: "compact" });
    expect(JSON.parse(localStorage.getItem("aiind:prefs")!).theme).toBe("dark");
    setPrefs({ theme: "neon" as any });
    expect(readPrefs().theme).toBe("system");
  });

  it("manages watchlists: add, toggle, reorder", () => {
    const id = addList("Banks");
    expect(activeList(readPrefs()).name).toBe("Banks");
    toggleInList("SBIN");
    toggleInList("HDFCBANK");
    moveInList(id, "HDFCBANK", -1);
    expect(activeList(readPrefs()).symbols).toEqual(["HDFCBANK", "SBIN"]);
    toggleInList("SBIN");
    expect(activeList(readPrefs()).symbols).toEqual(["HDFCBANK"]);
    expect(readPrefs().watchlists[0].symbols).toEqual(DEFAULT_PREFS.watchlists[0].symbols);
  });

  it("the boot script paints the saved theme before React", () => {
    localStorage.setItem("aiind:prefs", JSON.stringify({ theme: "dark" }));
    document.documentElement.classList.remove("dark");
    // eslint-disable-next-line no-new-func
    new Function(THEME_BOOT)();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});

describe("command palette ranking", () => {
  const c = (label: string, keywords = ""): Command => ({ id: label, label, keywords, group: "Pages", run: () => {} });
  it("puts prefix matches first, then word and substring matches", () => {
    const out = rankCommands("al", [c("Signals & risk"), c("Price alerts"), c("AI learning", "models"), c("Alerts setup")]);
    expect(out.map((x) => x.label)).toEqual(["Alerts setup", "Price alerts", "Signals & risk"]);
    expect(rankCommands("", [c("A"), c("B")])).toHaveLength(2);
    expect(rankCommands("zzz", [c("A")])).toHaveLength(0);
  });
});

describe("order ticket maths", () => {
  it("value, risk to stop and reward:risk", () => {
    const m = ticketMath({ side: "BUY", qty: 10, price: 100, stop: 95, target: 110 });
    expect(m.value).toBe(1000);
    expect(m.risk).toBe(50);
    expect(m.rr).toBe(2);
    expect(m.problems).toEqual([]);
  });
  it("flags a stop above the price and a zero quantity", () => {
    expect(ticketMath({ side: "BUY", qty: 0, price: 100, stop: 105 }).problems).toEqual([
      "Quantity must be at least 1", "Stop-loss must be below the price"]);
  });
});

describe("helpers", () => {
  it("SSE frames are split on blank lines and keep the rest", () => {
    const { frames, rest } = parseFrames('retry: 3000\n\ndata: {"a":1}\n\nevent: error\ndata: {"error":"x"}\n\ndata: {"b"');
    expect(frames).toEqual([{ event: "message", data: '{"a":1}' }, { event: "error", data: '{"error":"x"}' }]);
    expect(rest).toBe('data: {"b"');
  });

  it("only today's candle follows the live price", () => {
    const now = Date.UTC(2026, 9, 12, 6, 0);
    const today = now / 1000;
    const c = [{ t: today - 86400, o: 1, h: 2, l: 1, c: 1.5 }, { t: today - 60, o: 1, h: 2, l: 1, c: 1.8 }];
    const out = withLivePrice(c, 2.5, now);
    expect(out[1]).toMatchObject({ c: 2.5, h: 2.5 });
    expect(withLivePrice([c[0]], 2.5, now)).toEqual([c[0]]);
  });

  it("calendar has Mon–Fri columns ending today", () => {
    const cols = buildCalendar([{ date: "2026-10-09", pnl: 120 }], 2, new Date("2026-10-12T10:00:00Z"));
    expect(cols).toHaveLength(2);
    expect(cols[0]).toHaveLength(5);
    expect(cols[0].find((d) => d.date === "2026-10-09")?.pnl).toBe(120);
    expect(cols[1].map((d) => d.date)).toEqual(["2026-10-12"]);
  });

  it("alert distance, heat colours and sector names", () => {
    expect(alertDistance({ op: "above", value: 110 }, 100)).toBeCloseTo(10);
    expect(alertDistance({ op: "pct_up", value: 2 }, 100)).toBeNull();
    expect(heatColor(0.05)).toContain("0 179 134");
    expect(heatColor(-0.01)).toContain("235 91 60");
    expect(sectorName("OIL_GAS")).toBe("Oil & Gas");
    expect(sectorName("IT")).toBe("IT");
  });
});

describe("ui", () => {
  it("Sheet closes on Escape and the backdrop", () => {
    const onClose = vi.fn();
    render(<Sheet open onClose={onClose} title="Hello"><p>body</p></Sheet>);
    expect(screen.getByText("body")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText("Close"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("Tabs switch and the gauge shows the probability", () => {
    const onChange = vi.fn();
    render(<Tabs value="a" onChange={onChange} options={[{ value: "a", label: "A" }, { value: "b", label: "B", count: 3 }]} />);
    fireEvent.click(screen.getByText("B"));
    expect(onChange).toHaveBeenCalledWith("b");
    render(<Gauge value={0.62} threshold={0.55} label="P(win)" />);
    expect(screen.getByText("62%")).toBeTruthy();
  });

  it("toasts show and can be dismissed", async () => {
    function Fire() {
      const toast = useToast();
      return <button type="button" onClick={() => toast({ tone: "ok", title: "Saved" })}>go</button>;
    }
    render(<ToastProvider><Fire /></ToastProvider>);
    fireEvent.click(screen.getByText("go"));
    await waitFor(() => expect(screen.getByText("Saved")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("Dismiss"));
    expect(screen.queryByText("Saved")).toBeNull();
  });
});
