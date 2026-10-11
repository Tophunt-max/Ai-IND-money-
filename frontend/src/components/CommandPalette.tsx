"use client";

import type { LucideIcon } from "lucide-react";
import { ArrowRight, Bell, BellPlus, CornerDownLeft, Keyboard, Moon, Radar, Search, ShoppingCart, Sun } from "lucide-react";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { resolveTheme, setPrefs, usePrefs } from "@/lib/prefs";
import { useSymbolSearch } from "@/lib/search";

import { useAuth } from "./AuthGate";
import { ALL_PAGES } from "./nav";
import { useNotifications } from "./Notifications";
import { useOrderTicket } from "./OrderTicket";
import { cx, Sheet, SymbolAvatar } from "./ui";

export interface Command {
  id: string;
  label: string;
  group: "Stocks" | "Pages" | "Actions";
  icon?: LucideIcon;
  symbol?: string;
  hint?: string;
  keywords?: string;
  run: () => void;
}

/** Commands matching *query*: prefix matches first, then words, then anywhere (exported for tests). */
export function rankCommands(query: string, commands: Command[]): Command[] {
  const q = query.trim().toLowerCase();
  if (!q) return commands;
  const score = (c: Command) => {
    const hay = `${c.label} ${c.keywords || ""}`.toLowerCase();
    if (c.label.toLowerCase().startsWith(q)) return 0;
    if (hay.split(/[\s/&-]+/).some((w) => w.startsWith(q))) return 1;
    if (hay.includes(q)) return 2;
    return -1;
  };
  return commands.map((c) => ({ c, s: score(c) })).filter((x) => x.s >= 0)
    .sort((a, b) => a.s - b.s).map((x) => x.c);
}

interface Ctx { open: () => void; help: () => void }
const PaletteCtx = createContext<Ctx>({ open: () => {}, help: () => {} });
export const usePalette = () => useContext(PaletteCtx);

const SHORTCUTS: [string, string][] = [
  ["Ctrl K  ·  /", "Search stocks, pages and actions"],
  ["g h · g m · g p · g a", "Home · Market · Portfolio · AI insights"],
  ["g o · g t · g l · g s", "Options · Trade desk · Alerts · Settings"],
  ["b", "New order"],
  ["n", "Notifications"],
  ["d", "Light / dark theme"],
  ["?", "This help"],
];

function typing(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  return !!t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
}

export function CommandPaletteProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [help, setHelp] = useState(false);
  const router = useRouter();
  const ticket = useOrderTicket();
  const notif = useNotifications();
  const [prefs] = usePrefs();
  const lead = useRef<{ key: string; at: number } | null>(null);

  const toggleTheme = useCallback(() => {
    setPrefs({ theme: resolveTheme(prefs.theme) === "dark" ? "light" : "dark" });
  }, [prefs.theme]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((x) => !x);
        return;
      }
      if (typing(e) || e.metaKey || e.ctrlKey || e.altKey) return;
      const now = Date.now();
      if (lead.current && lead.current.key === "g" && now - lead.current.at < 1200) {
        const page = ALL_PAGES.find((p) => p.keys === `g ${e.key.toLowerCase()}`);
        lead.current = null;
        if (page) {
          e.preventDefault();
          router.push(page.href);
        }
        return;
      }
      switch (e.key) {
        case "/": e.preventDefault(); setOpen(true); break;
        case "?": e.preventDefault(); setHelp(true); break;
        case "g": lead.current = { key: "g", at: now }; break;
        case "b": e.preventDefault(); ticket(); break;
        case "n": e.preventDefault(); notif.open(); break;
        case "d": e.preventDefault(); toggleTheme(); break;
        default: break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router, ticket, notif, toggleTheme]);

  const ctx = useMemo(() => ({ open: () => setOpen(true), help: () => setHelp(true) }), []);
  return (
    <PaletteCtx.Provider value={ctx}>
      {children}
      {open && <Palette onClose={() => setOpen(false)} toggleTheme={toggleTheme} showHelp={() => { setOpen(false); setHelp(true); }} />}
      <Sheet open={help} onClose={() => setHelp(false)} title={<span className="flex items-center gap-2"><Keyboard className="h-5 w-5 text-brand-400" /> Keyboard shortcuts</span>}>
        <ul className="divide-y divide-white/[0.05]">
          {SHORTCUTS.map(([k, what]) => (
            <li key={k} className="flex items-center justify-between gap-4 py-2.5 text-sm">
              <span className="text-gray-300">{what}</span>
              <span className="flex shrink-0 flex-wrap justify-end gap-1">{k.split("·").map((x) => <span key={x} className="kbd">{x.trim()}</span>)}</span>
            </li>
          ))}
        </ul>
      </Sheet>
    </PaletteCtx.Provider>
  );
}

function Palette({ onClose, toggleTheme, showHelp }: { onClose: () => void; toggleTheme: () => void; showHelp: () => void }) {
  const router = useRouter();
  const { isAdmin } = useAuth();
  const ticket = useOrderTicket();
  const notif = useNotifications();
  const [prefs] = usePrefs();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const { results } = useSymbolSearch(q, true, 8);
  const go = (href: string) => { onClose(); router.push(href); };
  const dark = resolveTheme(prefs.theme) === "dark";

  const commands = useMemo<Command[]>(() => {
    const stocks: Command[] = results.map((r) => ({
      id: `s-${r.symbol}`, label: r.symbol, group: "Stocks", symbol: r.symbol, hint: r.kind === "index" ? r.name : r.name.replace(/_/g, " ").toLowerCase(),
      keywords: r.name, run: () => go(`/market?symbol=${encodeURIComponent(r.symbol)}`),
    }));
    const pages: Command[] = ALL_PAGES.filter((p) => !p.admin || isAdmin).map((p) => ({
      id: `p-${p.href}`, label: p.label, group: "Pages", icon: p.icon, hint: p.keys, keywords: `${p.href} ${p.hint || ""}`, run: () => go(p.href),
    }));
    const actions: Command[] = [
      ...(isAdmin ? [{ id: "a-order", label: "New order", group: "Actions" as const, icon: ShoppingCart, hint: "b", keywords: "buy sell trade ticket", run: () => { onClose(); ticket(); } }] : []),
      { id: "a-alert", label: "Set a price alert", group: "Actions", icon: BellPlus, keywords: "notify trigger", run: () => go("/alerts") },
      { id: "a-notif", label: "Notifications", group: "Actions", icon: Bell, hint: "n", run: () => { onClose(); notif.open(); } },
      { id: "a-scan", label: "Run a swing scan", group: "Actions", icon: Radar, keywords: "ideas ai", run: () => go("/scanner") },
      { id: "a-theme", label: dark ? "Light theme" : "Dark theme", group: "Actions", icon: dark ? Sun : Moon, hint: "d", keywords: "appearance mode", run: () => { toggleTheme(); onClose(); } },
      { id: "a-keys", label: "Keyboard shortcuts", group: "Actions", icon: Keyboard, hint: "?", run: showHelp },
    ];
    // Stock results come pre-ranked by the server; pages and actions are filtered here
    return [...(q.trim() ? stocks : []), ...rankCommands(q, [...pages, ...actions])];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results, q, isAdmin, dark]);

  useEffect(() => setSel(0), [q]);
  const groups = (["Stocks", "Pages", "Actions"] as const).map((g) => ({ g, items: commands.filter((c) => c.group === g) })).filter((x) => x.items.length);
  const flat = groups.flatMap((x) => x.items);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-3 pt-[10vh]" role="dialog" aria-modal="true" aria-label="Command palette">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative w-full max-w-xl overflow-hidden rounded-3xl bg-ink-900 shadow-pop animate-scale-in">
        <div className="flex items-center gap-3 border-b border-white/[0.06] px-4">
          <Search className="h-5 w-5 text-gray-500" />
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search stocks, pages, actions…"
            className="h-14 w-full bg-transparent text-[15px] text-white outline-none placeholder:text-gray-500"
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              else if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(flat.length - 1, s + 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
              else if (e.key === "Enter") flat[sel]?.run();
            }} />
          <span className="kbd hidden sm:inline-flex">Esc</span>
        </div>
        <div className="max-h-[60vh] overflow-y-auto p-2">
          {flat.length === 0 && <p className="px-3 py-8 text-center text-sm text-gray-500">Nothing matches “{q}”.</p>}
          {groups.map(({ g, items }) => (
            <div key={g} className="mb-1">
              <div className="px-3 pb-1 pt-2 text-[11px] font-semibold text-gray-500">{g}</div>
              {items.map((c) => {
                const i = flat.indexOf(c);
                const Icon = c.icon;
                return (
                  <button key={c.id} type="button" onMouseEnter={() => setSel(i)} onClick={c.run}
                    className={cx("flex w-full items-center gap-3 rounded-xl px-3 py-2 text-left text-sm transition", i === sel ? "bg-brand-500/10 text-white" : "text-gray-300")}>
                    {c.symbol ? <SymbolAvatar symbol={c.symbol} size={28} /> : Icon ? <span className="grid h-7 w-7 place-items-center rounded-lg bg-white/[0.05]"><Icon className="h-4 w-4 text-gray-400" /></span> : null}
                    <span className="min-w-0 flex-1 truncate font-medium">{c.label}{c.group === "Stocks" && c.hint && <span className="ml-2 text-xs font-normal text-gray-500">{c.hint}</span>}</span>
                    {c.group !== "Stocks" && c.hint && <span className="kbd">{c.hint}</span>}
                    {i === sel && (c.group === "Stocks" ? <ArrowRight className="h-4 w-4 text-brand-400" /> : <CornerDownLeft className="h-4 w-4 text-brand-400" />)}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
