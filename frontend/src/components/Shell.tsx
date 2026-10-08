"use client";

import type { LucideIcon } from "lucide-react";
import {
  BarChart3, Brain, CalendarClock, FlaskConical, Grid2x2, Layers, LayoutDashboard, LineChart,
  ListChecks, LogOut, MessagesSquare, PlugZap, Radar, ScrollText, Settings, SlidersHorizontal,
  Sparkles, Wallet, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { useApi } from "@/lib/hooks";

import { useAuth } from "./AuthGate";
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
      { href: "/portfolio", label: "Portfolio", icon: Wallet },
      { href: "/market", label: "Market", icon: LineChart },
    ],
  },
  {
    group: "Trading",
    items: [
      { href: "/analyze", label: "Analyze", icon: Brain },
      { href: "/scanner", label: "Scanner", icon: Radar },
      { href: "/options", label: "Options", icon: Layers },
      { href: "/trades", label: "Trades", icon: ScrollText },
    ],
  },
  {
    group: "Insights",
    items: [
      { href: "/report", label: "Track record", icon: BarChart3 },
      { href: "/learning", label: "AI learning", icon: Sparkles },
      { href: "/backtest", label: "Backtest", icon: FlaskConical },
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

const TABS: NavItem[] = [
  { href: "/", label: "Home", icon: LayoutDashboard },
  { href: "/portfolio", label: "Portfolio", icon: Wallet },
  { href: "/analyze", label: "Analyze", icon: Brain },
  { href: "/chat", label: "Chat", icon: MessagesSquare },
];

function isActive(path: string, href: string): boolean {
  if (href === "/") return path === "/";
  if (href === "/settings") return path === "/settings";
  return path === href || path.startsWith(href + "/");
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2.5">
      <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-brand-500 to-cyan-400 shadow-glow">
        <span className="text-sm font-black text-white">AI</span>
      </div>
      <div className="leading-tight">
        <div className="text-[15px] font-semibold tracking-tight text-white">AI IND Money</div>
        <div className="text-[11px] text-gray-500">Multi-agent trading</div>
      </div>
    </Link>
  );
}

function ModeBadge({ mode }: { mode?: string }) {
  if (!mode) return null;
  return mode === "live" ? <Badge tone="error" dot>LIVE</Badge> : <Badge tone="warning" dot>PAPER</Badge>;
}

function NavList({ path, isAdmin, onNavigate }: { path: string; isAdmin: boolean; onNavigate?: () => void }) {
  return (
    <nav className="space-y-4">
      {NAV.map((section) => (
        <div key={section.group}>
          <div className="px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-gray-600">{section.group}</div>
          <div className="space-y-0.5">
            {section.items.filter((i) => !i.admin || isAdmin).map((item) => {
              const active = isActive(path, item.href);
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={onNavigate}
                  className={cx(
                    "group flex items-center gap-3 rounded-xl px-3 py-[7px] text-sm font-medium transition",
                    active ? "bg-gradient-to-r from-brand-500/20 to-transparent text-white ring-1 ring-inset ring-brand-500/25"
                      : "text-gray-400 hover:bg-white/[0.04] hover:text-gray-100",
                  )}
                >
                  <Icon className={cx("h-[18px] w-[18px]", active ? "text-brand-300" : "text-gray-500 group-hover:text-gray-300")} />
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

function UserBox() {
  const { user, signOut } = useAuth();
  if (!user) return null;
  return (
    <div className="flex items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
      <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gradient-to-br from-ink-600 to-ink-700 text-sm font-semibold uppercase text-gray-200 ring-1 ring-white/10">
        {(user.name || user.email).slice(0, 1)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium text-gray-200">{user.name || user.email}</div>
        <div className="text-[10px] uppercase tracking-wider text-gray-500">{user.role}</div>
      </div>
      <button onClick={() => signOut(false)} title="Logout" className="rounded-lg p-1.5 text-gray-500 hover:bg-white/[0.06] hover:text-gray-200">
        <LogOut className="h-4 w-4" />
      </button>
    </div>
  );
}

export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { isAdmin, user } = useAuth();
  const [drawer, setDrawer] = useState(false);
  const me = useApi<{ mode: string }>(user ? "/api/dashboard/me" : null, 60000);
  const mode = me.data?.mode;

  useEffect(() => setDrawer(false), [path]);

  if (path.startsWith("/reset-password")) return <>{children}</>;

  return (
    <div className="min-h-screen">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-white/[0.05] bg-ink-950/80 backdrop-blur-xl lg:flex">
        <div className="flex items-center justify-between px-5 py-4">
          <Brand />
        </div>
        <div className="flex-1 overflow-y-auto px-3 pb-4">
          <NavList path={path} isAdmin={isAdmin} />
        </div>
        <div className="space-y-2.5 border-t border-white/[0.05] p-3">
          <div className="flex items-center justify-between px-1 text-xs text-gray-500">
            <span>Trading mode</span>
            <ModeBadge mode={mode} />
          </div>
          <UserBox />
        </div>
      </aside>

      {/* Mobile top bar */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-white/[0.05] bg-ink-950/80 px-4 py-3 backdrop-blur-xl lg:hidden">
        <Brand />
        <div className="flex items-center gap-2">
          <ModeBadge mode={mode} />
          {user?.role === "viewer" && <Badge>VIEW ONLY</Badge>}
        </div>
      </header>

      <main className="lg:pl-64">
        <div className="mx-auto max-w-6xl px-4 py-6 pb-28 md:px-8 md:py-8 lg:pb-12">{children}</div>
      </main>

      {/* Mobile bottom tabs */}
      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-white/[0.06] bg-ink-950/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl lg:hidden">
        <div className="grid grid-cols-5">
          {TABS.map((t) => {
            const active = isActive(path, t.href);
            const Icon = t.icon;
            return (
              <Link key={t.href} href={t.href} className={cx("flex flex-col items-center gap-1 py-2.5 text-[10px] font-medium", active ? "text-brand-300" : "text-gray-500")}>
                <Icon className="h-5 w-5" />
                {t.label}
              </Link>
            );
          })}
          <button onClick={() => setDrawer(true)} className={cx("flex flex-col items-center gap-1 py-2.5 text-[10px] font-medium", drawer ? "text-brand-300" : "text-gray-500")}>
            <Grid2x2 className="h-5 w-5" />
            More
          </button>
        </div>
      </nav>

      {/* Mobile drawer */}
      {drawer && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setDrawer(false)} />
          <div className="absolute inset-x-0 bottom-0 max-h-[85vh] overflow-y-auto rounded-t-3xl border-t border-white/10 bg-ink-900 p-4 pb-[calc(1rem+env(safe-area-inset-bottom))] animate-fade-in">
            <div className="mb-4 flex items-center justify-between">
              <Brand />
              <button onClick={() => setDrawer(false)} className="rounded-lg p-2 text-gray-400 hover:bg-white/[0.06]">
                <X className="h-5 w-5" />
              </button>
            </div>
            <NavList path={path} isAdmin={isAdmin} onNavigate={() => setDrawer(false)} />
            <div className="mt-6"><UserBox /></div>
          </div>
        </div>
      )}
    </div>
  );
}
