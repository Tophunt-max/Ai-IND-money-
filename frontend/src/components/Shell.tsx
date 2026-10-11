"use client";

import {
  ArrowLeftRight, Bell, ChevronDown, Grid2x2, Keyboard, LogOut, Moon, OctagonX, Search, Settings, Sun, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useApi } from "@/lib/hooks";
import { LiveQuotesProvider, useQuotes, useLiveState } from "@/lib/live";
import { useMarketOpen } from "@/lib/market";
import { resolveTheme, setPrefs, usePrefs } from "@/lib/prefs";

import { useAuth } from "./AuthGate";
import { CommandPaletteProvider, usePalette } from "./CommandPalette";
import { useModeChanged } from "./ModeSwitch";
import { isActive, NAV, PRIMARY, type NavItem } from "./nav";
import { NotificationsProvider, useNotifications } from "./Notifications";
import OfflineBanner from "./OfflineBanner";
import { OrderTicketProvider, useOrderTicket } from "./OrderTicket";
import { ToastProvider } from "./Toast";
import { Badge, cx, Flash, IconButton } from "./ui";

export { NAV };

// ── Theme, density and motion follow the preferences ──────────────────────────

function ThemeSync() {
  const [p] = usePrefs();
  useEffect(() => {
    const apply = () => {
      const c = document.documentElement.classList;
      c.toggle("dark", resolveTheme(p.theme) === "dark");
      c.toggle("density-compact", p.density === "compact");
      c.toggle("reduce-motion", p.reduceMotion);
      const meta = document.querySelector('meta[name="theme-color"]');
      meta?.setAttribute("content", resolveTheme(p.theme) === "dark" ? "#0d0f13" : "#f3f5f9");
    };
    apply();
    if (p.theme !== "system" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener?.("change", apply);
    return () => mq.removeEventListener?.("change", apply);
  }, [p.theme, p.density, p.reduceMotion]);
  return null;
}

function ThemeButton() {
  const [p] = usePrefs();
  const dark = resolveTheme(p.theme) === "dark";
  return <IconButton icon={dark ? Sun : Moon} label={dark ? "Light theme (d)" : "Dark theme (d)"} onClick={() => setPrefs({ theme: dark ? "light" : "dark" })} />;
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link href="/" className="flex shrink-0 items-center gap-2.5">
      <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-brand-400 to-brand-600 shadow-sm shadow-brand-500/30">
        <span className="text-[13px] font-black tracking-tight text-onaccent">AI</span>
      </div>
      {!compact && (
        <div className="leading-tight">
          <div className="text-[15px] font-bold tracking-tight text-white">AI IND Money</div>
          <div className="text-[11px] text-gray-500">Adaptive AI trading</div>
        </div>
      )}
    </Link>
  );
}

function ModeBadge({ mode }: { mode?: string }) {
  const { isAdmin } = useAuth();
  if (!mode) return null;
  const badge = mode === "live" ? <Badge tone="error" dot>Live</Badge> : <Badge tone="warning" dot>Paper</Badge>;
  return isAdmin ? <Link href="/settings" title="Switch paper / live" className="transition hover:opacity-80">{badge}</Link> : badge;
}

interface IndexQuote { name: string; symbol: string; ltp: number | null; change_pct: number | null }

/** NIFTY / BANKNIFTY / VIX with the market status, live from the price stream. */
function MarketStrip({ compact = false }: { compact?: boolean }) {
  const open = useMarketOpen();
  const { user } = useAuth();
  const router = useRouter();
  const idx = useApi<{ indices: IndexQuote[] }>(user ? "/api/dashboard/market/indices" : null, 300000);
  const rows = idx.data?.indices || [];
  const { quotes } = useQuotes(rows.map((r) => r.symbol));
  const live = useLiveState();
  return (
    <div className={cx("no-scrollbar flex items-center gap-2 overflow-x-auto whitespace-nowrap", compact ? "text-[11px]" : "text-xs")}>
      <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-[11px] font-semibold",
        open ? "bg-emerald-500/10 text-emerald-400" : "bg-white/[0.05] text-gray-500")} title={`Prices: ${live.state}`}>
        <span className={cx("h-1.5 w-1.5 rounded-full", open ? "animate-pulse bg-emerald-400" : "bg-gray-500")} />
        {open ? "Market open" : "Market closed"}
      </span>
      {rows.map((i) => {
        const q = quotes[i.symbol];
        const ltp = q?.ltp ?? i.ltp;
        const pct = q?.change_pct ?? i.change_pct;
        const up = (pct ?? 0) >= 0;
        return (
          <button key={i.name} type="button" onClick={() => router.push(`/market?symbol=${encodeURIComponent(i.symbol)}`)}
            className="num inline-flex items-baseline gap-1.5 rounded-full px-2 py-1 transition hover:bg-white/[0.05]">
            <span className="font-sans font-medium text-gray-500">{i.name.replace("NIFTY 50", "NIFTY").replace("India ", "")}</span>
            <span className="font-semibold text-gray-100"><Flash value={ltp}>{ltp?.toLocaleString("en-IN", { maximumFractionDigits: 2 }) ?? "—"}</Flash></span>
            {pct != null && <span className={up ? "text-emerald-400" : "text-rose-400"}>{up ? "+" : "−"}{Math.abs(pct * 100).toFixed(2)}%</span>}
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
    <Link href="/control" title={ks.data.text} className="inline-flex items-center gap-1 rounded-full bg-rose-500/15 px-2 py-1 text-[11px] font-bold text-rose-300">
      <OctagonX className="h-3.5 w-3.5" /> Halted
    </Link>
  );
}

function useOutside(ref: React.RefObject<HTMLElement>, onOut: () => void, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const fn = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && onOut();
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, [ref, onOut, active]);
}

/** Every other page, grouped (desktop "More" menu and the phone sheet). */
function AppGrid({ path, isAdmin, onNavigate, skipPrimary = false }: { path: string; isAdmin: boolean; onNavigate: () => void; skipPrimary?: boolean }) {
  return (
    <div className="space-y-5">
      {NAV.filter((s) => !skipPrimary || s.group !== "Invest").map((section) => (
        <div key={section.group}>
          <div className="pb-2 text-xs font-semibold text-gray-500">{section.group}</div>
          <div className="grid grid-cols-4 gap-2 sm:grid-cols-5">
            {section.items.filter((i) => !i.admin || isAdmin).map((item) => {
              const active = isActive(path, item.href);
              const Icon = item.icon;
              return (
                <Link key={item.href} href={item.href} onClick={onNavigate}
                  className={cx("flex flex-col items-center gap-1.5 rounded-2xl px-1 py-3 text-center text-[11px] font-medium leading-tight transition",
                    active ? "bg-brand-500/10 text-brand-300" : "text-gray-300 hover:bg-white/[0.04]")}>
                  <span className={cx("grid h-10 w-10 place-items-center rounded-xl", active ? "bg-brand-500 text-onaccent" : "bg-white/[0.05] text-gray-400")}>
                    <Icon className="h-5 w-5" />
                  </span>
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

function MoreMenu({ path, isAdmin }: { path: string; isAdmin: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, () => setOpen(false), open);
  useEffect(() => setOpen(false), [path]);
  const inMore = !PRIMARY.some((p) => isActive(path, p.href));
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)}
        className={cx("inline-flex items-center gap-1 rounded-xl px-3 py-2 text-sm font-semibold transition",
          inMore || open ? "text-white" : "text-gray-500 hover:text-white")}>
        More <ChevronDown className={cx("h-4 w-4 transition", open && "rotate-180")} />
      </button>
      {open && (
        <div className="absolute left-1/2 top-full z-50 mt-2 w-[min(640px,90vw)] -translate-x-1/2 rounded-3xl border border-white/[0.06] bg-ink-900 p-5 shadow-pop animate-scale-in">
          <AppGrid path={path} isAdmin={isAdmin} onNavigate={() => setOpen(false)} skipPrimary />
        </div>
      )}
    </div>
  );
}

function UserMenu({ mode }: { mode?: string }) {
  const { user, signOut } = useAuth();
  const palette = usePalette();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutside(ref, () => setOpen(false), open);
  if (!user) return null;
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-label="Account"
        className="grid h-9 w-9 place-items-center rounded-full bg-gradient-to-br from-brand-400/30 to-brand-600/30 text-sm font-bold uppercase text-brand-300 ring-2 ring-transparent transition hover:ring-brand-500/30">
        {(user.name || user.email).slice(0, 1)}
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-64 rounded-2xl border border-white/[0.06] bg-ink-900 p-2 shadow-pop animate-scale-in">
          <div className="px-3 py-2">
            <div className="truncate text-sm font-semibold text-white">{user.name || user.email}</div>
            <div className="mt-1 flex items-center gap-2 text-xs text-gray-500"><span className="capitalize">{user.role}</span> · <ModeBadge mode={mode} /></div>
          </div>
          <div className="my-1 h-px bg-white/[0.06]" />
          <Link href="/settings" onClick={() => setOpen(false)} className="flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm text-gray-300 hover:bg-white/[0.05]"><Settings className="h-4 w-4" /> Settings</Link>
          <button type="button" onClick={() => { setOpen(false); palette.help(); }} className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-sm text-gray-300 hover:bg-white/[0.05]"><Keyboard className="h-4 w-4" /> Keyboard shortcuts</button>
          <button type="button" onClick={() => signOut(false)} className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-sm text-rose-300 hover:bg-rose-500/10"><LogOut className="h-4 w-4" /> Log out</button>
        </div>
      )}
    </div>
  );
}

function SearchButton({ wide = false }: { wide?: boolean }) {
  const palette = usePalette();
  if (!wide) return <IconButton icon={Search} label="Search (Ctrl K)" onClick={palette.open} />;
  return (
    <button type="button" onClick={palette.open}
      className="flex h-10 w-full max-w-xs items-center gap-2 rounded-xl border border-white/[0.08] bg-ink-850 px-3 text-sm text-gray-500 transition hover:border-brand-500/40">
      <Search className="h-4 w-4" />
      <span className="flex-1 truncate text-left">Search</span>
      <span className="kbd">Ctrl K</span>
    </button>
  );
}

function BellButton() {
  const n = useNotifications();
  return <IconButton icon={Bell} label="Notifications (n)" onClick={n.open} badge={n.unread} />;
}

function Tab({ item, path }: { item: NavItem; path: string }) {
  const active = isActive(path, item.href);
  const Icon = item.icon;
  return (
    <Link href={item.href} className={cx("flex flex-col items-center gap-1 py-2 text-[11px] font-semibold transition", active ? "text-brand-400" : "text-gray-500")}>
      <span className={cx("grid h-7 w-12 place-items-center rounded-full transition", active && "bg-brand-500/10")}><Icon className="h-5 w-5" /></span>
      {item.label === "AI insights" ? "AI" : item.label}
    </Link>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { isAdmin, user } = useAuth();
  const [drawer, setDrawer] = useState(false);
  const ticket = useOrderTicket();
  const me = useApi<{ mode: string }>(user ? "/api/dashboard/me" : null, 60000);
  const mode = me.data?.mode;
  useEffect(() => setDrawer(false), [path]);
  useModeChanged(me.reload);
  const tabs = [PRIMARY[0], PRIMARY[1], null, PRIMARY[3], PRIMARY[2]];

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-white/[0.06] bg-ink-900/85 pt-[env(safe-area-inset-top)] backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-3 sm:px-4 lg:h-16 lg:px-6">
          <div className="lg:hidden"><Brand compact /></div>
          <div className="hidden lg:block"><Brand /></div>
          <nav className="ml-4 hidden items-center gap-1 lg:flex">
            {PRIMARY.map((p) => (
              <Link key={p.href} href={p.href}
                className={cx("relative rounded-xl px-3 py-2 text-sm font-semibold transition",
                  isActive(path, p.href) ? "text-white" : "text-gray-500 hover:text-white")}>
                {p.label}
                {isActive(path, p.href) && <span className="absolute inset-x-3 -bottom-[13px] h-0.5 rounded-full bg-brand-500" />}
              </Link>
            ))}
            <MoreMenu path={path} isAdmin={isAdmin} />
          </nav>
          <div className="hidden min-w-0 flex-1 justify-end xl:flex"><SearchButton wide /></div>
          <div className="ml-auto flex shrink-0 items-center gap-1 xl:ml-2">
            <HaltPill />
            <span className="hidden sm:inline"><ModeBadge mode={mode} /></span>
            {user?.role === "viewer" && <Badge>View only</Badge>}
            <span className="xl:hidden"><SearchButton /></span>
            <BellButton />
            <ThemeButton />
            <span className="hidden lg:inline"><UserMenu mode={mode} /></span>
            <span className="lg:hidden"><IconButton icon={Grid2x2} label="All pages" onClick={() => setDrawer(true)} /></span>
          </div>
        </div>
        <div className="mx-auto max-w-7xl px-2 pb-2 sm:px-3 lg:px-5"><MarketStrip compact /></div>
      </header>

      <main>
        <OfflineBanner />
        <div className="mx-auto max-w-7xl px-3 py-4 pb-28 sm:px-4 md:px-6 md:py-6 lg:pb-12">{children}</div>
      </main>

      {/* Phone: bottom tabs with a centre Trade button that opens the order ticket */}
      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-white/[0.06] bg-ink-900/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl lg:hidden">
        <div className="grid grid-cols-5 items-end">
          {tabs.map((t, i) => t ? <Tab key={t.href} item={t} path={path} /> : (
            <button key={`trade-${i}`} type="button" onClick={() => (isAdmin ? ticket() : undefined)} aria-label="Trade" disabled={!isAdmin}
              className="flex flex-col items-center gap-1 pb-2 text-[11px] font-semibold text-gray-400 disabled:opacity-50">
              <span className="-mt-5 grid h-14 w-14 place-items-center rounded-full bg-gradient-to-br from-brand-400 to-brand-600 shadow-lg shadow-brand-500/30 ring-4 ring-ink-950 transition active:scale-95">
                <ArrowLeftRight className="h-6 w-6 text-onaccent" />
              </span>
              Trade
            </button>
          ))}
        </div>
      </nav>

      {drawer && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/50 backdrop-blur-[2px]" onClick={() => setDrawer(false)} />
          <div className="absolute inset-x-0 bottom-0 max-h-[88vh] overflow-y-auto rounded-t-3xl bg-ink-900 p-5 pb-[calc(1.25rem+env(safe-area-inset-bottom))] shadow-sheet animate-slide-up">
            <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-white/15" />
            <div className="mb-5 flex items-center justify-between">
              <Brand />
              <div className="flex items-center gap-1">
                <ModeBadge mode={mode} />
                <IconButton icon={X} label="Close" onClick={() => setDrawer(false)} />
              </div>
            </div>
            <AppGrid path={path} isAdmin={isAdmin} onNavigate={() => setDrawer(false)} />
            <MobileAccount />
          </div>
        </div>
      )}
    </div>
  );
}

function MobileAccount() {
  const { user, signOut } = useAuth();
  if (!user) return null;
  return (
    <div className="mt-6 flex items-center gap-3 rounded-2xl bg-white/[0.04] p-3">
      <div className="grid h-10 w-10 place-items-center rounded-full bg-brand-500/15 text-sm font-bold uppercase text-brand-300">{(user.name || user.email).slice(0, 1)}</div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-white">{user.name || user.email}</div>
        <div className="text-xs capitalize text-gray-500">{user.role}</div>
      </div>
      <IconButton icon={LogOut} label="Log out" onClick={() => signOut(false)} />
    </div>
  );
}

function LiveWithAlerts({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const notif = useNotifications();
  return <LiveQuotesProvider enabled={!!user} onAlert={notif.onAlerts}>{children}</LiveQuotesProvider>;
}

/** Providers every page shares, then the frame (header, tabs, sheets). */
export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { user } = useAuth();
  if (path.startsWith("/reset-password") || path.startsWith("/offline")) return <><ThemeSync />{children}</>;
  return (
    <ToastProvider>
      <ThemeSync />
      <NotificationsProvider enabled={!!user}>
        <LiveWithAlerts>
          <OrderTicketProvider>
            <CommandPaletteProvider>
              <Frame>{children}</Frame>
            </CommandPaletteProvider>
          </OrderTicketProvider>
        </LiveWithAlerts>
      </NotificationsProvider>
    </ToastProvider>
  );
}
