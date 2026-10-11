import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/supabase", () => ({ accessToken: async () => "tok" }));
vi.mock("@/components/AuthGate", () => ({ useAuth: () => ({ isAdmin: true, user: null }) }));

import CandleChart from "@/components/CandleChart";
import OfflineBanner from "@/components/OfflineBanner";
import RiskCapacity from "@/components/RiskCapacity";
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
