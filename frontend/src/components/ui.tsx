"use client";

import type { LucideIcon } from "lucide-react";
import {
  AlertTriangle, ArrowDownRight, ArrowUpRight, CheckCircle2, ChevronDown, Info, Inbox, Loader2, X, XCircle,
} from "lucide-react";
import { useEffect, useRef, useState, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

export type Tone = "ok" | "warning" | "error" | "neutral" | "info";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");
export { cx };

/** Text colour for a P&L number. */
export function pnlClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "text-gray-300";
  return v > 0 ? "text-emerald-400" : v < 0 ? "text-rose-400" : "text-gray-300";
}

/** "+1.23%" / "−0.40%" with an arrow, coloured. ``value`` is a fraction (0.0123 = 1.23 %). */
export function Change({ value, abs, size = "sm", pill = true }: {
  value: number | null | undefined; abs?: number | null; size?: "xs" | "sm" | "md"; pill?: boolean;
}) {
  if (value == null || Number.isNaN(value)) return <span className="num text-gray-500">—</span>;
  const up = value >= 0;
  const Icon = up ? ArrowUpRight : ArrowDownRight;
  const s = { xs: "text-[11px]", sm: "text-xs", md: "text-sm" }[size];
  return (
    <span className={cx("num inline-flex items-center gap-0.5 whitespace-nowrap font-semibold", s,
      up ? "text-emerald-400" : "text-rose-400",
      pill && "rounded-full px-2 py-0.5", pill && (up ? "bg-emerald-500/10" : "bg-rose-500/10"))}>
      <Icon className={size === "md" ? "h-4 w-4" : "h-3 w-3"} />
      {abs != null && <span className="mr-0.5">{abs >= 0 ? "+" : "−"}{Math.abs(abs).toLocaleString("en-IN", { maximumFractionDigits: 2 })}</span>}
      {abs != null ? "(" : ""}{up ? "+" : "−"}{(Math.abs(value) * 100).toFixed(2)}%{abs != null ? ")" : ""}
    </span>
  );
}

/** Briefly tints its children green / red when *value* moves (live prices). */
export function Flash({ value, children, className }: { value: number | null | undefined; children: ReactNode; className?: string }) {
  const prev = useRef(value);
  const [dir, setDir] = useState<"up" | "down" | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    if (value != null && prev.current != null && value !== prev.current) {
      setDir(value > prev.current ? "up" : "down");
      setN((x) => x + 1);
    }
    prev.current = value;
  }, [value]);
  return (
    <span key={n} className={cx("rounded px-0.5 -mx-0.5", dir === "up" && "animate-flash-up", dir === "down" && "animate-flash-down", className)}>
      {children}
    </span>
  );
}

/** A round symbol avatar with a stable colour. */
export function SymbolAvatar({ symbol, size = 40 }: { symbol: string; size?: number }) {
  const hues = [232, 160, 20, 280, 340, 190, 40, 120];
  let h = 0;
  for (const ch of symbol) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const hue = hues[h % hues.length];
  return (
    <div className="grid shrink-0 place-items-center rounded-full font-bold"
      style={{ width: size, height: size, fontSize: size * 0.32, background: `hsl(${hue} 70% 55% / 0.14)`, color: `hsl(${hue} 65% 50%)` }}>
      {symbol.replace("^", "").replace(/[^A-Z0-9]/g, "").slice(0, 2)}
    </div>
  );
}

// ── Page structure ────────────────────────────────────────────────────────────

export function PageTitle({
  title, subtitle, icon: Icon, right,
}: { title: string; subtitle?: ReactNode; icon?: LucideIcon; right?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3 sm:mb-5">
      <div className="flex min-w-0 items-center gap-3">
        {Icon && (
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-brand-500/10">
            <Icon className="h-5 w-5 text-brand-400" />
          </div>
        )}
        <div className="min-w-0">
          <h1 className="truncate text-xl font-bold tracking-tight text-white sm:text-2xl">{title}</h1>
          {subtitle && <p className="mt-0.5 line-clamp-2 text-[13px] text-gray-500">{subtitle}</p>}
        </div>
      </div>
      {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
    </div>
  );
}

export function Card({
  title, subtitle, icon: Icon, children, className = "", right, padded = true,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  icon?: LucideIcon;
  children: ReactNode;
  className?: string;
  right?: ReactNode;
  padded?: boolean;
}) {
  return (
    <section className={cx("surface animate-fade-in", padded && "p-4 sm:p-5", className)}>
      {(title || right) && (
        <div className={cx("mb-4 flex items-start justify-between gap-3", !padded && "px-4 pt-4 sm:px-5 sm:pt-5")}>
          <div className="flex min-w-0 items-center gap-2.5">
            {Icon && (
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-brand-500/10">
                <Icon className="h-4 w-4 text-brand-400" />
              </span>
            )}
            <div className="min-w-0">
              {title && <h3 className="text-[15px] font-semibold tracking-tight text-white">{title}</h3>}
              {subtitle && <p className="mt-0.5 text-xs leading-snug text-gray-500">{subtitle}</p>}
            </div>
          </div>
          {right && <div className="shrink-0">{right}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

const STAT_ICON: Record<Tone, string> = {
  ok: "bg-emerald-500/10 text-emerald-400", warning: "bg-amber-500/10 text-amber-400", error: "bg-rose-500/10 text-rose-400",
  neutral: "bg-white/[0.05] text-gray-400", info: "bg-brand-500/10 text-brand-400",
};

/** A metric tile: label, big value, detail, a tinted icon for the tone. */
export function StatCard({
  title, value, detail, tone = "neutral", icon: Icon,
}: {
  title: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: Tone;
  icon?: LucideIcon;
}) {
  return (
    <div className="surface relative overflow-hidden p-4 animate-fade-in">
      <div className="flex items-start justify-between gap-2">
        <span className="truncate text-xs font-medium text-gray-500">{title}</span>
        {Icon && <span className={cx("grid h-7 w-7 shrink-0 place-items-center rounded-lg", STAT_ICON[tone])}><Icon className="h-3.5 w-3.5" /></span>}
      </div>
      <div className="num mt-1 break-words text-lg font-bold text-white sm:text-xl">{value}</div>
      {detail && <div className="mt-0.5 break-words text-xs text-gray-500">{detail}</div>}
    </div>
  );
}

export function Badge({ children, tone = "neutral", dot = false }: { children: ReactNode; tone?: Tone; dot?: boolean }) {
  const cls: Record<Tone, string> = {
    ok: "bg-emerald-500/10 text-emerald-300",
    warning: "bg-amber-500/10 text-amber-300",
    error: "bg-rose-500/10 text-rose-300",
    neutral: "bg-white/[0.06] text-gray-300",
    info: "bg-brand-500/10 text-brand-300",
  };
  const dots: Record<Tone, string> = {
    ok: "bg-emerald-400", warning: "bg-amber-400", error: "bg-rose-400", neutral: "bg-gray-400", info: "bg-brand-400",
  };
  return (
    <span className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold", cls[tone])}>
      {dot && <span className={cx("h-1.5 w-1.5 rounded-full", dots[tone])} />}
      {children}
    </span>
  );
}

// ── Controls ──────────────────────────────────────────────────────────────────

export function Button({
  children, onClick, disabled, variant = "primary", size = "md", type = "button", className = "",
  icon: Icon, loading = false, title,
}: {
  children?: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "danger" | "ghost" | "subtle" | "success" | "soft";
  size?: "sm" | "md" | "lg";
  type?: "button" | "submit";
  className?: string;
  icon?: LucideIcon;
  loading?: boolean;
  title?: string;
}) {
  const v = {
    primary: "bg-brand-500 text-onaccent shadow-sm shadow-brand-500/20 hover:bg-brand-600",
    danger: "bg-rose-500 text-onaccent shadow-sm shadow-rose-500/20 hover:bg-rose-600",
    success: "bg-emerald-500 text-onaccent shadow-sm shadow-emerald-500/20 hover:bg-emerald-600",
    ghost: "border border-white/[0.1] bg-ink-900 text-gray-200 hover:border-white/[0.18] hover:bg-ink-850",
    soft: "bg-brand-500/10 text-brand-300 hover:bg-brand-500/15",
    subtle: "text-gray-300 hover:bg-white/[0.06] hover:text-white",
  }[variant];
  const s = { sm: "h-8 px-3 text-xs gap-1.5", md: "h-10 px-4 text-sm gap-2", lg: "h-12 px-5 text-[15px] gap-2" }[size];
  const Ico = loading ? Loader2 : Icon;
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || loading}
      title={title}
      className={cx("inline-flex items-center justify-center rounded-xl font-semibold transition active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50", v, s, className)}
    >
      {Ico && <Ico className={cx(size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4", loading && "animate-spin")} />}
      {children}
    </button>
  );
}

export function IconButton({ icon: Icon, label, onClick, active = false, badge, className }: {
  icon: LucideIcon; label: string; onClick?: () => void; active?: boolean; badge?: number; className?: string;
}) {
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label}
      className={cx("relative grid h-10 w-10 place-items-center rounded-xl text-gray-400 transition hover:bg-white/[0.06] hover:text-white",
        active && "bg-white/[0.06] text-white", className)}>
      <Icon className="h-5 w-5" />
      {!!badge && badge > 0 && (
        <span className="num absolute right-1 top-1 grid h-4 min-w-4 place-items-center rounded-full bg-rose-500 px-1 text-[10px] font-bold text-onaccent">
          {badge > 9 ? "9+" : badge}
        </span>
      )}
    </button>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx("field", props.className)} />;
}

export function Select({ children, className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className={cx("relative", className)}>
      <select {...props} className="field appearance-none pr-9">{children}</select>
      <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
    </div>
  );
}

export function Field({ label, hint, children, className }: { label: string; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cx("block", className)}>
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-gray-500">{hint}</span>}
    </label>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)}
      className={cx("relative h-6 w-11 shrink-0 rounded-full transition", checked ? "bg-brand-500" : "bg-ink-600")}>
      <span className={cx("absolute top-0.5 h-5 w-5 rounded-full bg-onaccent shadow transition-all", checked ? "left-[22px]" : "left-0.5")} />
    </button>
  );
}

/** Pill tabs: one value of a few (scrolls sideways on a phone). */
export function Segmented<T extends string | number>({
  options, value, onChange, size = "md",
}: {
  options: readonly { value: T; label: ReactNode }[];
  value: T;
  onChange: (v: T) => void;
  size?: "sm" | "md";
}) {
  return (
    <div className="no-scrollbar inline-flex max-w-full overflow-x-auto rounded-xl bg-ink-850 p-1 ring-1 ring-inset ring-white/[0.06]">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          onClick={() => onChange(o.value)}
          className={cx(
            "whitespace-nowrap rounded-lg font-semibold transition",
            size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-1.5 text-[13px]",
            value === o.value ? "bg-ink-900 text-white shadow-card ring-1 ring-white/[0.06]" : "text-gray-500 hover:text-gray-200",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Underlined tabs (Groww-style section switcher). */
export function Tabs<T extends string>({ options, value, onChange, className }: {
  options: readonly { value: T; label: ReactNode; count?: number }[]; value: T; onChange: (v: T) => void; className?: string;
}) {
  return (
    <div className={cx("no-scrollbar flex gap-5 overflow-x-auto border-b border-white/[0.08]", className)} role="tablist">
      {options.map((o) => (
        <button key={o.value} type="button" role="tab" aria-selected={value === o.value} onClick={() => onChange(o.value)}
          className={cx("relative -mb-px whitespace-nowrap border-b-2 pb-2.5 pt-1 text-sm font-semibold transition",
            value === o.value ? "border-brand-500 text-white" : "border-transparent text-gray-500 hover:text-gray-200")}>
          {o.label}
          {o.count != null && <span className="num ml-1.5 rounded-full bg-white/[0.06] px-1.5 text-[11px] text-gray-400">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

/** A bottom sheet on a phone, a centred dialog on a desktop. Esc and the backdrop close it. */
export function Sheet({ open, onClose, title, children, wide = false, footer }: {
  open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode; wide?: boolean; footer?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center sm:p-4" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-[2px] animate-fade-in" onClick={onClose} />
      <div className={cx("relative flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-3xl bg-ink-900 shadow-pop animate-slide-up sm:rounded-3xl sm:animate-scale-in",
        wide ? "sm:max-w-3xl" : "sm:max-w-md")}>
        <div className="mx-auto mt-2.5 h-1 w-10 shrink-0 rounded-full bg-white/15 sm:hidden" />
        {title && (
          <div className="flex shrink-0 items-center justify-between gap-3 px-5 pb-2 pt-3 sm:pt-5">
            <div className="min-w-0 text-base font-bold text-white">{title}</div>
            <button type="button" onClick={onClose} aria-label="Close" className="rounded-lg p-1.5 text-gray-500 hover:bg-white/[0.06] hover:text-white">
              <X className="h-5 w-5" />
            </button>
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">{children}</div>
        {footer && <div className="shrink-0 border-t border-white/[0.06] px-5 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))]">{footer}</div>}
      </div>
    </div>
  );
}

// ── Feedback ──────────────────────────────────────────────────────────────────

const NOTICE: Record<Tone, { cls: string; icon: LucideIcon }> = {
  ok: { cls: "border-emerald-500/20 bg-emerald-500/[0.07] text-emerald-200", icon: CheckCircle2 },
  warning: { cls: "border-amber-500/25 bg-amber-500/[0.08] text-amber-200", icon: AlertTriangle },
  error: { cls: "border-rose-500/25 bg-rose-500/[0.07] text-rose-200", icon: XCircle },
  neutral: { cls: "border-white/[0.08] bg-white/[0.03] text-gray-300", icon: Info },
  info: { cls: "border-brand-500/20 bg-brand-500/[0.07] text-brand-200", icon: Info },
};

export function Notice({ tone = "info", title, children, icon, className, action }: {
  tone?: Tone; title?: ReactNode; children?: ReactNode; icon?: LucideIcon; className?: string; action?: ReactNode;
}) {
  const n = NOTICE[tone];
  const Icon = icon || n.icon;
  return (
    <div className={cx("flex items-start gap-3 rounded-2xl border p-3.5 text-[13px] animate-fade-in", n.cls, className)}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1 break-words">
        {title && <div className="font-semibold">{title}</div>}
        {children && <div className={cx(!!title && "mt-0.5", "opacity-90")}>{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return <Notice tone="error" className="mb-3">{error}</Notice>;
}

export function Loading({ text = "Loading..." }: { text?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-gray-400">
      <Loader2 className="h-4 w-4 animate-spin text-brand-400" /> {text}
    </div>
  );
}

export function Skeleton({ className = "h-4 w-full" }: { className?: string }) {
  return (
    <div className={cx("relative overflow-hidden rounded-xl bg-white/[0.05]", className)}>
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/[0.05] to-transparent" />
    </div>
  );
}

export function Empty({ children, icon: Icon = Inbox, title }: { children?: ReactNode; icon?: LucideIcon; title?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-2xl bg-white/[0.05]">
        <Icon className="h-5 w-5 text-gray-500" />
      </div>
      {title && <div className="text-sm font-semibold text-gray-200">{title}</div>}
      {children && <div className="max-w-md text-[13px] text-gray-500">{children}</div>}
    </div>
  );
}

// ── Data display ──────────────────────────────────────────────────────────────

/** Label / value rows. */
export function KV({ items, cols = 1 }: { items: [ReactNode, ReactNode][]; cols?: 1 | 2 | 3 }) {
  const grid = { 1: "", 2: "grid-cols-2", 3: "grid-cols-2 sm:grid-cols-3" }[cols];
  return (
    <dl className={cx("grid gap-x-4 gap-y-3 text-[13px]", grid)}>
      {items.map(([k, v], i) => (
        <div key={i} className={cx(cols === 1 ? "flex items-center justify-between gap-3" : "min-w-0")}>
          <dt className="text-xs text-gray-500">{k}</dt>
          <dd className={cx("num break-words font-semibold text-gray-100", cols === 1 && "text-right")}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Data table; scrolls sideways on narrow screens with the first column pinned. */
export function Table({ head, children, className }: { head: ReactNode[]; children: ReactNode; className?: string }) {
  return (
    <div className={cx("-mx-4 overflow-x-auto sm:-mx-5", className)}>
      <table className="w-full min-w-[540px] text-[13px]">
        <thead>
          <tr className="border-b border-white/[0.06] text-left text-xs text-gray-500">
            {head.map((h, i) => (
              <th key={i} className={cx("whitespace-nowrap px-4 py-2.5 font-medium sm:px-5", i > 0 && "text-right",
                i === 0 && "sticky left-0 z-[1] bg-ink-900")}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.05] [&_td]:px-4 [&_td]:py-2.5 sm:[&_td]:px-5 [&_td:not(:first-child)]:text-right [&_td]:num [&_td:first-child]:sticky [&_td:first-child]:left-0 [&_td:first-child]:bg-ink-900">
          {children}
        </tbody>
      </table>
    </div>
  );
}

export function Progress({ value, tone = "info" }: { value: number; tone?: Tone }) {
  const bar = { ok: "bg-emerald-400", warning: "bg-amber-400", error: "bg-rose-400", neutral: "bg-gray-400", info: "bg-brand-400" }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.07]">
      <div className={cx("h-full rounded-full transition-all", bar)} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

export function actionTone(action?: string | null): Tone {
  const a = (action || "").toUpperCase();
  if (a === "BUY") return "ok";
  if (a === "SELL") return "error";
  return "warning";
}

/** Long text, collapsed by default. */
export function Collapsible({ title, text }: { title: string; text: string }) {
  return (
    <details className="group rounded-2xl border border-white/[0.06] bg-white/[0.02] open:bg-white/[0.03]">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-[13px] font-medium text-gray-200">
        {title}
        <ChevronDown className="h-4 w-4 text-gray-500 transition group-open:rotate-180" />
      </summary>
      <pre className="whitespace-pre-wrap px-4 pb-4 font-sans text-[13px] leading-relaxed text-gray-400">{text}</pre>
    </details>
  );
}

/** A half-ring gauge for a probability (0..1) against a threshold. */
export function Gauge({ value, threshold, size = 120, label }: { value: number | null; threshold?: number | null; size?: number; label?: string }) {
  const r = size / 2 - 10;
  const c = Math.PI * r;
  const v = value == null ? 0 : Math.max(0, Math.min(1, value));
  const tone = value == null ? "text-gray-500" : threshold != null && v >= threshold ? "text-emerald-400" : v >= 0.5 ? "text-amber-400" : "text-rose-400";
  const tAngle = threshold != null ? Math.PI * (1 - Math.max(0, Math.min(1, threshold))) : null;
  return (
    <div className="relative inline-block" style={{ width: size, height: size / 2 + 18 }}>
      <svg width={size} height={size / 2 + 10} viewBox={`0 0 ${size} ${size / 2 + 10}`}>
        <path d={`M10 ${size / 2} A ${r} ${r} 0 0 1 ${size - 10} ${size / 2}`} fill="none" strokeWidth={10} strokeLinecap="round" className="stroke-white/[0.08]" />
        <path d={`M10 ${size / 2} A ${r} ${r} 0 0 1 ${size - 10} ${size / 2}`} fill="none" strokeWidth={10} strokeLinecap="round"
          stroke="currentColor" className={tone} strokeDasharray={`${c * v} ${c}`} />
        {tAngle != null && (
          <line x1={size / 2 + (r - 9) * Math.cos(tAngle)} y1={size / 2 - (r - 9) * Math.sin(tAngle)}
            x2={size / 2 + (r + 9) * Math.cos(tAngle)} y2={size / 2 - (r + 9) * Math.sin(tAngle)} strokeWidth={2} className="stroke-gray-300" />
        )}
      </svg>
      <div className="absolute inset-x-0 bottom-0 text-center">
        <div className={cx("num text-lg font-bold leading-none", tone)}>{value == null ? "—" : `${Math.round(v * 100)}%`}</div>
        {label && <div className="mt-0.5 text-[10px] text-gray-500">{label}</div>}
      </div>
    </div>
  );
}
