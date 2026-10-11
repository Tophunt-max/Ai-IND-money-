"use client";

import type { LucideIcon } from "lucide-react";
import {
  ArrowLeftRight, BarChart3, Brain, CalendarClock, CandlestickChart, FlaskConical, Gauge, Grid2x2, Layers,
  LayoutDashboard, ListChecks, LogOut, MessagesSquare, OctagonX, PlugZap, Radar, ScrollText, Settings,
  Signal, SlidersHorizontal, Sparkles, Wallet, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { useApi } from "@/lib/hooks";
import { useMarketOpen } from "@/lib/market";

import { useAuth } from "./AuthGate";
import OfflineBanner from "./OfflineBanner";
import { useModeChanged } from "./ModeSwitch";
import { Badge, cx } from "./ui";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  admin?: boolean;
}

export const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "Overview",
    items: [
      { href: "/", label: "Dashboard", icon: LayoutDashboard },
      { href: "/market", label: "Market", icon: CandlestickChart },
      { href: "/portfolio", label: "Portfolio", icon: Wallet },
    ],
  },
  {
    group: "Trading",
    items: [
      { href: "/control", label: "Trade desk", icon: Gauge },
      { href: "/options", label: "Options", icon: Layers },
      { href: "/signals", label: "Signals & risk", icon: Signal },
      { href: "/trades", label: "Trades", icon: ScrollText },
      { href: "/analyze", label: "AI analyze", icon: Brain },
      { href: "/scanner", label: "Scanner", icon: Radar },
    ],
  },
  {
    group: "Insights",
    items: [
      { href: "/report", label: "Track record", icon: BarChart3 },
      { href: "/backtest", label: "Backtest", icon: FlaskConical },
      { href: "/learning", label: "AI learning", icon: Sparkles },
    ],
  },
  {
    group: "Automation",
    items: [
      { href: "/scheduler", label: "Scheduler", icon: CalendarClock },
      { href: "/jobs", label: "Jobs", icon: ListChecks },
      { href: "/chat", label: "AI chat", icon: MessagesSquare },
    ],
  },
  {
    group: "System",
    items: [
      { href: "/broker", label: "Broker", icon: PlugZap },
      { href: "/settings", label: "Settings", icon: Settings },
      { href: "/settings/environment", label: "Environment", icon: SlidersHorizontal, admin: true },
    ],
  },
];

// Phone tabs: Home, Market, [Trade], Portfolio, More
const TABS_LEFT: NavItem[] = [
  { href: "/", label: "Home", icon: LayoutDashboard },
  { href: "/market", label: "Market", icon: CandlestickChart },
];
const TABS_RIGHT: NavItem[] = [
  { href: "/portfolio", label: "Positions", icon: Wallet },
];

function isActive(path: string, href: string): boolean {
  if (href === "/") return path === "/";
  if (href === "/settings") return path === "/settings";
  return path === href || path.startsWith(href + "/");
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/" className="flex items-center gap-2">
      <div className="grid h-8 w-8 place-items-center rounded-lg bg-brand-500">
        <span className="text-xs font-black tracking-tight text-white">AI</span>
      </div>
      {!compact && (
        <div className="leading-tight">
          <div className="text-sm font-bold tracking-tight text-white">AI IND Money</div>
          <div className="text-[10px] uppercase tracking-wider text-gray-500">NSE · algo desk</div>
        </div>
      )}
    </Link>
  );
}

function ModeBadge({ mode }: { mode?: string }) {
  const { isAdmin } = useAuth();
  if (!mode) return null;
  const badge = mode === "live" ? <Badge tone="error" dot>LIVE</Badge> : <Badge tone="warning" dot>PAPER</Badge>;
  // Admins switch paper / live on the Settings page
  return isAdmin ? <Link href="/settings" title="Switch paper / live" className="transition hover:opacity-80">{badge}</Link> : badge;
}

interface IndexQuote { name: string; symbol: string; ltp: number | null; change_pct: number | null; source?: string }

/** NIFTY / BANKNIFTY / VIX strip with the market status (every page). */
function Ticker({ compact = false }: { compact?: boolean }) {
  const open = useMarketOpen();
  const { user } = useAuth();
  const router = useRouter();
  const idx = useApi<{ indices: IndexQuote[] }>(user ? "/api/dashboard/market/indices" : null, open ? 15000 : 120000);
  const rows = idx.data?.indices || [];
  return (
    <div className={cx("no-scrollbar flex items-center gap-4 overflow-x-auto whitespace-nowrap", compact ? "text-[11px]" : "text-xs")}>
      <span className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider">
        <span className={cx("h-1.5 w-1.5 rounded-full", open ? "animate-pulse bg-emerald-400" : "bg-gray-600")} />
        <span className={open ? "text-emerald-300" : "text-gray-500"}>{open ? "NSE open" : "NSE closed"}</span>
      </span>
      {rows.map((i) => {
        const up = (i.change_pct ?? 0) >= 0;
        return (
          <button key={i.name} type="button" onClick={() => router.push(`/market?symbol=${encodeURIComponent(i.symbol)}`)}
            className="num flex items-baseline gap-1.5 hover:opacity-80">
            <span className="font-sans text-[10px] font-semibold uppercase text-gray-500">{i.name.replace("NIFTY 50", "NIFTY").replace("India ", "")}</span>
            <span className="font-semibold text-gray-100">{i.ltp?.toLocaleString("en-IN", { maximumFractionDigits: 2 }) ?? "—"}</span>
            {i.change_pct != null && (
              <span className={up ? "text-emerald-400" : "text-rose-400"}>{up ? "+" : "−"}{Math.abs(i.change_pct * 100).toFixed(2)}%</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

function HaltPill() {
  const { user } = useAuth();
  const ks = useApi<{ halted: boolean; text?: string }>(user ? "/api/dashboard/kill-switch" : null, 60000);
  if (!ks.data?.halted) return null;
  return (
    <Link href="/control" title={ks.data.text} className="inline-flex items-center gap-1 rounded bg-rose-500/15 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-rose-300 ring-1 ring-inset ring-rose-500/30">
      <OctagonX className="h-3 w-3" /> Halted
    </Link>
  );
}

function NavList({ path, isAdmin, onNavigate }: { path: string; isAdmin: boolean; onNavigate?: () => void }) {
  return (
    <nav className="space-y-3">
      {NAV.map((section) => (
        <div key={section.group}>
          <div className="px-2.5 pb-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-600">{section.group}</div>
          <div className="space-y-px">
            {section.items.filter((i) => !i.admin || isAdmin).map((item) => {
              const active = isActive(path, item.href);
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onNavigate}
                  className={cx(
                    "group relative flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] font-medium transition",
                    active ? "bg-white/[0.06] text-white before:absolute before:inset-y-1 before:left-0 before:w-0.5 before:rounded before:bg-brand-400"
                      : "text-gray-400 hover:bg-white/[0.03] hover:text-gray-100",
                  )}
                >
                  <Icon className={cx("h-4 w-4", active ? "text-brand-300" : "text-gray-500 group-hover:text-gray-300")} />
                  {item.label}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

/** The phone's "More" sheet: every page as an app tile. */
function AppGrid({ path, isAdmin, onNavigate }: { path: string; isAdmin: boolean; onNavigate: () => void }) {
  return (
    <div className="space-y-4">
      {NAV.map((section) => (
        <div key={section.group}>
          <div className="pb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-500">{section.group}</div>
          <div className="grid grid-cols-4 gap-2">
            {section.items.filter((i) => !i.admin || isAdmin).map((item) => {
              const active = isActive(path, item.href);
              const Icon = item.icon;
              return (
                <Link key={item.href} href={item.href} onClick={onNavigate}
                  className={cx("flex flex-col items-center gap-1.5 rounded-lg px-1 py-2.5 text-center text-[10px] font-medium leading-tight transition",
                    active ? "bg-brand-500/15 text-white ring-1 ring-inset ring-brand-500/30" : "bg-white/[0.03] text-gray-300 active:bg-white/[0.08]")}>
                  <Icon className={cx("h-5 w-5", active ? "text-brand-300" : "text-gray-400")} />
                  {item.label}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function UserBox() {
  const { user, signOut } = useAuth();
  if (!user) return null;
  return (
    <div className="flex items-center gap-2.5 rounded-lg border border-white/[0.06] bg-white/[0.02] p-2">
      <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-ink-700 text-xs font-semibold uppercase text-gray-200">
        {(user.name || user.email).slice(0, 1)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium text-gray-200">{user.name || user.email}</div>
        <div className="text-[10px] uppercase tracking-wider text-gray-500">{user.role}</div>
      </div>
      <button onClick={() => signOut(false)} title="Logout" className="rounded-md p-1.5 text-gray-500 hover:bg-white/[0.06] hover:text-gray-200">
        <LogOut className="h-4 w-4" />
      </button>
    </div>
  );
}

function Tab({ item, path }: { item: NavItem; path: string }) {
  const active = isActive(path, item.href);
  const Icon = item.icon;
  return (
    <Link href={item.href} className={cx("flex flex-col items-center gap-0.5 py-2 text-[10px] font-semibold", active ? "text-brand-300" : "text-gray-500")}>
      <Icon className="h-5 w-5" />
      {item.label}
    </Link>
  );
}

export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { isAdmin, user } = useAuth();
  const [drawer, setDrawer] = useState(false);
  const me = useApi<{ mode: string }>(user ? "/api/dashboard/me" : null, 60000);
  const mode = me.data?.mode;

  useEffect(() => setDrawer(false), [path]);
  useModeChanged(me.reload);

  if (path.startsWith("/reset-password")) return <>{children}</>;
  const tradeActive = isActive(path, "/control");

  return (
    <div className="min-h-screen">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-56 flex-col border-r border-white/[0.06] bg-ink-950 lg:flex">
        <div className="flex h-12 items-center border-b border-white/[0.06] px-3">
          <Brand />
        </div>
        <div className="flex-1 overflow-y-auto px-2 py-3">
          <NavList path={path} isAdmin={isAdmin} />
        </div>
        <div className="space-y-2 border-t border-white/[0.06] p-2">
          <div className="flex items-center justify-between px-1 text-[11px] text-gray-500">
            <span>Trading mode</span>
            <ModeBadge mode={mode} />
          </div>
          <UserBox />
        </div>
      </aside>

      {/* Top bar: brand (phone), index ticker, mode */}
      <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-ink-950/95 pt-[env(safe-area-inset-top)] backdrop-blur lg:pl-56">
        <div className="flex h-11 items-center gap-3 px-3 lg:h-12 lg:px-5">
          <div className="lg:hidden"><Brand compact /></div>
          <div className="min-w-0 flex-1"><Ticker compact /></div>
          <div className="flex shrink-0 items-center gap-1.5">
            <HaltPill />
            <ModeBadge mode={mode} />
            {user?.role === "viewer" && <Badge>VIEW</Badge>}
          </div>
        </div>
      </header>

      <main className="lg:pl-56">
        <OfflineBanner />
        <div className="mx-auto max-w-7xl px-3 py-3 pb-28 sm:px-4 md:px-6 md:py-5 lg:pb-10">{children}</div>
      </main>

      {/* Phone bottom tabs with a centre Trade button */}
      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-white/[0.06] bg-ink-950/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
        <div className="grid grid-cols-5 items-end">
          {TABS_LEFT.map((t) => <Tab key={t.href} item={t} path={path} />)}
          <Link href="/control" aria-label="Trade" className="flex flex-col items-center gap-0.5 pb-2 text-[10px] font-semibold">
            <span className={cx("-mt-4 grid h-12 w-12 place-items-center rounded-full ring-4 ring-ink-950 transition",
              tradeActive ? "bg-brand-400" : "bg-brand-500")}>
              <ArrowLeftRight className="h-5 w-5 text-white" />
            </span>
            <span className={tradeActive ? "text-brand-300" : "text-gray-400"}>Trade</span>
          </Link>
          {TABS_RIGHT.map((t) => <Tab key={t.href} item={t} path={path} />)}
          <button onClick={() => setDrawer(true)} className={cx("flex flex-col items-center gap-0.5 py-2 text-[10px] font-semibold", drawer ? "text-brand-300" : "text-gray-500")}>
            <Grid2x2 className="h-5 w-5" />
            More
          </button>
        </div>
      </nav>

      {/* Phone "More" sheet */}
      {drawer && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/70" onClick={() => setDrawer(false)} />
          <div className="absolute inset-x-0 bottom-0 max-h-[88vh] overflow-y-auto rounded-t-2xl border-t border-white/10 bg-ink-900 p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] shadow-sheet animate-slide-up">
            <div className="mx-auto mb-3 h-1 w-10 rounded-full bg-white/15" />
            <div className="mb-4 flex items-center justify-between">
              <Brand />
              <div className="flex items-center gap-2">
                <ModeBadge mode={mode} />
                <button onClick={() => setDrawer(false)} aria-label="Close" className="rounded-md p-2 text-gray-400 hover:bg-white/[0.06]">
                  <X className="h-5 w-5" />
                </button>
              </div>
            </div>
            <AppGrid path={path} isAdmin={isAdmin} onNavigate={() => setDrawer(false)} />
            <div className="mt-5"><UserBox /></div>
          </div>
        </div>
      )}
    </div>
  );
}
