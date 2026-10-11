"use client";

import { Check, Download, LayoutGrid, LineChart, ListPlus, Monitor, Moon, Palette, Pencil, RotateCcw, Smartphone, Star, Sun, Trash2 } from "lucide-react";

import { STUDIES } from "@/lib/indicators";
import { addList, HOME_CARDS, LANDINGS, removeList, renameList, resetPrefs, setPrefs, usePrefs, type IndicatorKey, type Theme } from "@/lib/prefs";
import { promptInstall, usePwa } from "@/lib/pwa";

import { useToast } from "./Toast";
import { Button, Card, cx, Segmented, Select, Toggle } from "./ui";

function Row({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="min-w-0"><div className="text-sm font-medium text-gray-100">{title}</div>{hint && <div className="text-xs text-gray-500">{hint}</div>}</div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

const THEMES: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light", icon: Sun }, { value: "dark", label: "Dark", icon: Moon }, { value: "system", label: "System", icon: Monitor },
];

export function Appearance() {
  const [p] = usePrefs();
  return (
    <Card title="Appearance" icon={Palette} subtitle="Kept on this device">
      <div className="grid grid-cols-3 gap-2">
        {THEMES.map((t) => (
          <button key={t.value} type="button" onClick={() => setPrefs({ theme: t.value })}
            className={cx("flex flex-col items-center gap-2 rounded-2xl border p-3 text-sm font-semibold transition",
              p.theme === t.value ? "border-brand-500 bg-brand-500/10 text-brand-300" : "border-white/[0.08] text-gray-400 hover:text-white")}>
            <span className={cx("grid h-16 w-full place-items-center rounded-xl", t.value === "dark" ? "bg-[#0d0f13]" : t.value === "light" ? "bg-[#f3f5f9]" : "bg-gradient-to-r from-[#f3f5f9] to-[#0d0f13]")}>
              <t.icon className={cx("h-5 w-5", t.value === "dark" ? "text-[#a5b0ff]" : "text-[#5367ff]")} />
            </span>
            {t.label}
          </button>
        ))}
      </div>
      <div className="mt-2 divide-y divide-white/[0.05]">
        <Row title="Density" hint="Compact fits more on a screen"><Segmented size="sm" value={p.density} onChange={(v) => setPrefs({ density: v })} options={[{ value: "comfortable", label: "Comfortable" }, { value: "compact", label: "Compact" }]} /></Row>
        <Row title="Flash price changes" hint="Green / red blink when a live price moves"><Toggle label="Flash price changes" checked={p.flashPrices} onChange={(v) => setPrefs({ flashPrices: v })} /></Row>
        <Row title="Reduce motion" hint="No animations"><Toggle label="Reduce motion" checked={p.reduceMotion} onChange={(v) => setPrefs({ reduceMotion: v })} /></Row>
      </div>
    </Card>
  );
}

export function LayoutPrefs() {
  const [p] = usePrefs();
  const toggle = (k: string) => setPrefs((x) => ({ homeCards: x.homeCards.includes(k) ? x.homeCards.filter((c) => c !== k) : [...x.homeCards, k] }));
  return (
    <Card title="Layout" icon={LayoutGrid} subtitle="What you see first">
      <Row title="Open the app on">
        <Select value={p.landing} onChange={(e) => setPrefs({ landing: e.target.value })} className="w-44">
          {LANDINGS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
        </Select>
      </Row>
      <div className="mb-2 mt-1 text-xs font-medium text-gray-500">Home sections</div>
      <div className="flex flex-wrap gap-2">
        {HOME_CARDS.map((c) => {
          const on = p.homeCards.includes(c.value);
          return (
            <button key={c.value} type="button" onClick={() => toggle(c.value)} className={cx("chip", on && "chip-active")}>
              {on && <Check className="h-3.5 w-3.5" />}{c.label}
            </button>
          );
        })}
      </div>
    </Card>
  );
}

export function ChartPrefs() {
  const [p] = usePrefs();
  const toggle = (k: IndicatorKey) => setPrefs((x) => ({ indicators: x.indicators.includes(k) ? x.indicators.filter((i) => i !== k) : [...x.indicators, k] }));
  return (
    <Card title="Charts" icon={LineChart} subtitle="Defaults for every price chart">
      <Row title="Chart type"><Segmented size="sm" value={p.chartType} onChange={(v) => setPrefs({ chartType: v })} options={[{ value: "candle", label: "Candles" }, { value: "line", label: "Line" }]} /></Row>
      <Row title="Default range">
        <Segmented size="sm" value={p.chartRange} onChange={(v) => setPrefs({ chartRange: v })}
          options={["1d", "5d", "1mo", "3mo", "1y"].map((r) => ({ value: r, label: r.toUpperCase() }))} />
      </Row>
      <div className="mb-2 mt-1 text-xs font-medium text-gray-500">Indicators</div>
      <div className="flex flex-wrap gap-2">
        {STUDIES.map((s) => {
          const on = p.indicators.includes(s.key as IndicatorKey);
          return <button key={s.key} type="button" onClick={() => toggle(s.key as IndicatorKey)} className={cx("chip", on && "chip-active")}>{on && <Check className="h-3.5 w-3.5" />}{s.label}</button>;
        })}
      </div>
    </Card>
  );
}

export function WatchlistPrefs() {
  const [p] = usePrefs();
  return (
    <Card title="Watchlists" icon={Star} subtitle="Up to 10 lists of 100 stocks, on this device"
      right={<Button size="sm" variant="soft" icon={ListPlus} onClick={() => { const n = prompt("Watchlist name:"); if (n) addList(n); }}>New</Button>}>
      <ul className="divide-y divide-white/[0.05]">
        {p.watchlists.map((w) => (
          <li key={w.id} className="flex items-center gap-3 py-2.5">
            <button type="button" onClick={() => setPrefs({ activeWatchlist: w.id })} aria-label="Make default"
              className={cx("grid h-6 w-6 place-items-center rounded-full border", p.activeWatchlist === w.id ? "border-brand-500 bg-brand-500 text-onaccent" : "border-white/[0.15]")}>
              {p.activeWatchlist === w.id && <Check className="h-3.5 w-3.5" />}
            </button>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold text-white">{w.name}</div>
              <div className="truncate text-xs text-gray-500">{w.symbols.length ? w.symbols.slice(0, 6).join(", ") + (w.symbols.length > 6 ? ` +${w.symbols.length - 6}` : "") : "empty"}</div>
            </div>
            <button type="button" aria-label="Rename" onClick={() => { const n = prompt("New name:", w.name); if (n) renameList(w.id, n); }} className="rounded-lg p-1.5 text-gray-500 hover:text-white"><Pencil className="h-4 w-4" /></button>
            {p.watchlists.length > 1 && <button type="button" aria-label="Delete" onClick={() => confirm(`Delete "${w.name}"?`) && removeList(w.id)} className="rounded-lg p-1.5 text-gray-500 hover:text-rose-400"><Trash2 className="h-4 w-4" /></button>}
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function InstallApp() {
  const pwa = usePwa();
  const toast = useToast();
  return (
    <Card title="Install the app" icon={Smartphone} subtitle="Full screen, home-screen icon, offline page">
      {pwa.installed ? <p className="text-sm text-emerald-400">Installed: you are using the app.</p> : pwa.canInstall ? (
        <Button icon={Download} onClick={async () => { const r = await promptInstall(); toast({ tone: r === "accepted" ? "ok" : "info", title: r === "accepted" ? "Installing…" : "Not installed" }); }}>Install AI IND Money</Button>
      ) : (
        <ul className="space-y-1.5 text-sm text-gray-400">
          <li><b className="text-gray-200">Android</b> (Chrome): ⋮ menu → Install app</li>
          <li><b className="text-gray-200">iPhone</b> (Safari): Share → Add to Home Screen</li>
          <li><b className="text-gray-200">Computer</b> (Chrome / Edge): the install icon in the address bar</li>
        </ul>
      )}
      <div className="mt-4 border-t border-white/[0.06] pt-3">
        <Button size="sm" variant="ghost" icon={RotateCcw} onClick={() => { if (confirm("Reset theme, layout, charts and watchlists on this device?")) { resetPrefs(); toast({ tone: "ok", title: "Preferences reset" }); } }}>Reset preferences</Button>
      </div>
    </Card>
  );
}
