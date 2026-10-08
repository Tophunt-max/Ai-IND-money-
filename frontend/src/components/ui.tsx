import type { LucideIcon } from "lucide-react";
import {
  AlertTriangle, CheckCircle2, ChevronDown, Info, Inbox, Loader2, XCircle,
} from "lucide-react";
import type { ReactNode, SelectHTMLAttributes, InputHTMLAttributes } from "react";

export type Tone = "ok" | "warning" | "error" | "neutral" | "info";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");
export { cx };

/** Text colour for a P&L number. */
export function pnlClass(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "text-gray-300";
  return v > 0 ? "text-emerald-400" : v < 0 ? "text-rose-400" : "text-gray-300";
}

// ── Page structure ────────────────────────────────────────────────────────────

export function PageTitle({
  title, subtitle, icon: Icon, right,
}: { title: string; subtitle?: ReactNode; icon?: LucideIcon; right?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4 animate-fade-in">
      <div className="flex items-center gap-3 min-w-0">
        {Icon && (
          <div className="hidden sm:grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-gradient-to-br from-brand-500/25 to-cyan-400/10 ring-1 ring-white/10">
            <Icon className="h-5 w-5 text-brand-300" />
          </div>
        )}
        <div className="min-w-0">
          <h2 className="text-2xl font-semibold tracking-tight text-white">{title}</h2>
          {subtitle && <p className="mt-0.5 text-sm text-gray-400">{subtitle}</p>}
        </div>
      </div>
      {right && <div className="flex items-center gap-2">{right}</div>}
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
    <section className={cx("surface animate-fade-in", padded && "p-5", className)}>
      {(title || right) && (
        <div className={cx("mb-4 flex items-start justify-between gap-3", !padded && "px-5 pt-5")}>
          <div className="flex items-center gap-2.5 min-w-0">
            {Icon && <Icon className="h-4 w-4 shrink-0 text-brand-300" />}
            <div className="min-w-0">
              {title && <h3 className="text-sm font-semibold text-gray-100">{title}</h3>}
              {subtitle && <p className="text-xs text-gray-500 mt-0.5">{subtitle}</p>}
            </div>
          </div>
          {right && <div className="shrink-0">{right}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

const STAT_TONES: Record<Tone, string> = {
  ok: "from-emerald-500/15 text-emerald-300",
  warning: "from-amber-500/15 text-amber-300",
  error: "from-rose-500/15 text-rose-300",
  neutral: "from-white/[0.04] text-gray-400",
  info: "from-brand-500/15 text-brand-300",
};

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
    <div className={cx("surface relative overflow-hidden p-4 animate-fade-in bg-gradient-to-br to-transparent", STAT_TONES[tone].split(" ")[0])}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-gray-400">{title}</span>
        {Icon && <Icon className={cx("h-4 w-4", STAT_TONES[tone].split(" ")[1])} />}
      </div>
      <div className="num mt-2 text-xl font-semibold tracking-tight text-white break-words">{value}</div>
      {detail && <div className="mt-1 text-xs text-gray-500 break-words">{detail}</div>}
    </div>
  );
}

export function Badge({ children, tone = "neutral", dot = false }: { children: ReactNode; tone?: Tone; dot?: boolean }) {
  const cls: Record<Tone, string> = {
    ok: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/25",
    warning: "bg-amber-500/10 text-amber-300 ring-amber-500/25",
    error: "bg-rose-500/10 text-rose-300 ring-rose-500/25",
    neutral: "bg-white/[0.05] text-gray-300 ring-white/10",
    info: "bg-brand-500/10 text-brand-300 ring-brand-500/25",
  };
  const dots: Record<Tone, string> = {
    ok: "bg-emerald-400", warning: "bg-amber-400", error: "bg-rose-400", neutral: "bg-gray-400", info: "bg-brand-400",
  };
  return (
    <span className={cx("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset", cls[tone])}>
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
  variant?: "primary" | "danger" | "ghost" | "subtle" | "success";
  size?: "sm" | "md" | "lg";
  type?: "button" | "submit";
  className?: string;
  icon?: LucideIcon;
  loading?: boolean;
  title?: string;
}) {
  const v = {
    primary: "bg-gradient-to-b from-brand-500 to-brand-600 text-white shadow-glow hover:brightness-110",
    danger: "bg-gradient-to-b from-rose-500 to-rose-600 text-white shadow-[0_8px_24px_-10px_rgba(244,63,94,0.6)] hover:brightness-110",
    success: "bg-gradient-to-b from-emerald-500 to-emerald-600 text-white hover:brightness-110",
    ghost: "border border-white/10 bg-white/[0.03] text-gray-200 hover:bg-white/[0.07]",
    subtle: "text-gray-300 hover:bg-white/[0.06] hover:text-white",
  }[variant];
  const s = { sm: "h-8 px-3 text-xs gap-1.5", md: "h-10 px-4 text-sm gap-2", lg: "h-12 px-5 text-sm gap-2" }[size];
  const Ico = loading ? Loader2 : Icon;
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || loading}
      title={title}
      className={cx("inline-flex items-center justify-center rounded-xl font-medium transition active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50", v, s, className)}
    >
      {Ico && <Ico className={cx(size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4", loading && "animate-spin")} />}
      {children}
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

/** Pill tabs: one value of a few. */
export function Segmented<T extends string | number>({
  options, value, onChange, size = "md",
}: {
  options: readonly { value: T; label: ReactNode }[];
  value: T;
  onChange: (v: T) => void;
  size?: "sm" | "md";
}) {
  return (
    <div className="inline-flex max-w-full overflow-x-auto rounded-xl border border-white/[0.06] bg-ink-850/80 p-1">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          onClick={() => onChange(o.value)}
          className={cx(
            "whitespace-nowrap rounded-lg font-medium transition",
            size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-1.5 text-sm",
            value === o.value ? "bg-white/[0.09] text-white shadow-sm ring-1 ring-white/10" : "text-gray-400 hover:text-gray-200",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ── Feedback ──────────────────────────────────────────────────────────────────

const NOTICE: Record<Tone, { cls: string; icon: LucideIcon }> = {
  ok: { cls: "border-emerald-500/25 bg-emerald-500/[0.07] text-emerald-200", icon: CheckCircle2 },
  warning: { cls: "border-amber-500/25 bg-amber-500/[0.07] text-amber-100", icon: AlertTriangle },
  error: { cls: "border-rose-500/30 bg-rose-500/[0.08] text-rose-200", icon: XCircle },
  neutral: { cls: "border-white/10 bg-white/[0.03] text-gray-300", icon: Info },
  info: { cls: "border-brand-500/25 bg-brand-500/[0.07] text-brand-100", icon: Info },
};

export function Notice({ tone = "info", title, children, icon, className, action }: {
  tone?: Tone; title?: ReactNode; children?: ReactNode; icon?: LucideIcon; className?: string; action?: ReactNode;
}) {
  const n = NOTICE[tone];
  const Icon = icon || n.icon;
  return (
    <div className={cx("flex items-start gap-3 rounded-2xl border p-4 text-sm animate-fade-in", n.cls, className)}>
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
  return <Notice tone="error" className="mb-4">{error}</Notice>;
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
    <div className={cx("relative overflow-hidden rounded-lg bg-white/[0.04]", className)}>
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />
    </div>
  );
}

export function Empty({ children, icon: Icon = Inbox, title }: { children?: ReactNode; icon?: LucideIcon; title?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
      <div className="grid h-11 w-11 place-items-center rounded-xl bg-white/[0.04] ring-1 ring-white/[0.06]">
        <Icon className="h-5 w-5 text-gray-500" />
      </div>
      {title && <div className="text-sm font-medium text-gray-300">{title}</div>}
      {children && <div className="max-w-md text-sm text-gray-500">{children}</div>}
    </div>
  );
}

// ── Data display ──────────────────────────────────────────────────────────────

/** Label / value rows. */
export function KV({ items, cols = 1 }: { items: [ReactNode, ReactNode][]; cols?: 1 | 2 | 3 }) {
  const grid = { 1: "", 2: "sm:grid-cols-2", 3: "sm:grid-cols-3" }[cols];
  return (
    <dl className={cx("grid gap-x-6 gap-y-3 text-sm", grid)}>
      {items.map(([k, v], i) => (
        <div key={i} className={cx(cols === 1 ? "flex items-center justify-between gap-3" : "")}>
          <dt className="text-xs text-gray-500">{k}</dt>
          <dd className={cx("num font-medium text-gray-100 break-words", cols === 1 && "text-right")}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Table({ head, children, className }: { head: ReactNode[]; children: ReactNode; className?: string }) {
  return (
    <div className={cx("-mx-5 overflow-x-auto", className)}>
      <table className="w-full min-w-[560px] text-sm">
        <thead>
          <tr className="border-b border-white/[0.06] text-left text-[11px] uppercase tracking-wider text-gray-500">
            {head.map((h, i) => (
              <th key={i} className={cx("px-5 py-2.5 font-medium", i > 0 && "text-right")}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04] [&_td]:px-5 [&_td]:py-3 [&_td:not(:first-child)]:text-right [&_td]:num">
          {children}
        </tbody>
      </table>
    </div>
  );
}

export function Progress({ value, tone = "info" }: { value: number; tone?: Tone }) {
  const bar = { ok: "bg-emerald-400", warning: "bg-amber-400", error: "bg-rose-400", neutral: "bg-gray-400", info: "bg-gradient-to-r from-brand-500 to-cyan-400" }[tone];
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-white/[0.06]">
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

/** Long agent text, collapsed by default. */
export function Collapsible({ title, text }: { title: string; text: string }) {
  return (
    <details className="group rounded-xl border border-white/[0.06] bg-white/[0.02] open:bg-white/[0.03]">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-medium text-gray-200">
        {title}
        <ChevronDown className="h-4 w-4 text-gray-500 transition group-open:rotate-180" />
      </summary>
      <pre className="whitespace-pre-wrap px-4 pb-4 font-sans text-[13px] leading-relaxed text-gray-400">{text}</pre>
    </details>
  );
}
