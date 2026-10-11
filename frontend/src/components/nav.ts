import type { LucideIcon } from "lucide-react";
import {
  Bell, BarChart3, BrainCircuit, CalendarClock, CandlestickChart, FlaskConical, Gauge, Grid3x3, Home, Layers,
  ListChecks, PlugZap, Radar, ScrollText, Settings, Signal, SlidersHorizontal, Sparkles, Wallet,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  admin?: boolean;
  keys?: string;      // "g m" style shortcut
  hint?: string;
}

/** Top-level destinations (desktop header tabs, phone bottom bar). */
export const PRIMARY: NavItem[] = [
  { href: "/", label: "Home", icon: Home, keys: "g h" },
  { href: "/market", label: "Market", icon: CandlestickChart, keys: "g m" },
  { href: "/portfolio", label: "Portfolio", icon: Wallet, keys: "g p" },
  { href: "/ai", label: "AI insights", icon: BrainCircuit, keys: "g a" },
  { href: "/options", label: "Options", icon: Layers, keys: "g o" },
  { href: "/control", label: "Trade desk", icon: Gauge, keys: "g t" },
];

export const NAV: { group: string; items: NavItem[] }[] = [
  { group: "Invest", items: PRIMARY },
  {
    group: "Trading",
    items: [
      { href: "/market/heatmap", label: "Heatmap", icon: Grid3x3, hint: "NIFTY 50 by sector" },
      { href: "/scanner", label: "Swing scanner", icon: Radar },
      { href: "/signals", label: "Signals & risk", icon: Signal },
      { href: "/trades", label: "Trades", icon: ScrollText },
      { href: "/alerts", label: "Price alerts", icon: Bell, keys: "g l" },
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
    ],
  },
  {
    group: "Account",
    items: [
      { href: "/broker", label: "Broker", icon: PlugZap },
      { href: "/settings", label: "Settings", icon: Settings, keys: "g s" },
      { href: "/settings/environment", label: "Environment", icon: SlidersHorizontal, admin: true },
    ],
  },
];

export const ALL_PAGES: NavItem[] = NAV.flatMap((g) => g.items);

export function isActive(path: string, href: string): boolean {
  if (href === "/") return path === "/";
  if (href === "/settings" || href === "/market") return path === href;
  return path === href || path.startsWith(href + "/");
}
