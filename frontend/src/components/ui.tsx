import type { ReactNode } from "react";

export function PageTitle({ title, right }: { title: string; right?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 mb-5">
      <h2 className="text-2xl font-bold">{title}</h2>
      {right}
    </div>
  );
}

export function Card({
  title,
  children,
  className = "",
  right,
}: {
  title?: string;
  children: ReactNode;
  className?: string;
  right?: ReactNode;
}) {
  return (
    <section className={`border border-gray-800 bg-gray-900/40 rounded-lg p-4 ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between mb-3">
          {title && <h3 className="text-sm font-semibold text-gray-300">{title}</h3>}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export type Tone = "ok" | "warning" | "error" | "neutral";

const TONES: Record<Tone, string> = {
  ok: "border-green-800 bg-green-900/10",
  warning: "border-yellow-800 bg-yellow-900/10",
  error: "border-red-800 bg-red-900/10",
  neutral: "border-gray-800 bg-gray-900/40",
};
const DOTS: Record<Tone, string> = {
  ok: "bg-green-400",
  warning: "bg-yellow-400",
  error: "bg-red-400",
  neutral: "bg-gray-500",
};

export function StatCard({
  title,
  value,
  detail,
  tone = "neutral",
}: {
  title: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: Tone;
}) {
  return (
    <div className={`border rounded-lg p-4 ${TONES[tone]}`}>
      <div className="flex items-center gap-2 mb-1">
        <div className={`w-2 h-2 rounded-full ${DOTS[tone]}`} />
        <span className="text-sm text-gray-400">{title}</span>
      </div>
      <div className="text-lg font-semibold break-words">{value}</div>
      {detail && <div className="text-xs text-gray-500 mt-1 break-words">{detail}</div>}
    </div>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: Tone }) {
  const cls: Record<Tone, string> = {
    ok: "bg-green-900/50 text-green-400",
    warning: "bg-yellow-900/50 text-yellow-400",
    error: "bg-red-900/50 text-red-400",
    neutral: "bg-gray-800 text-gray-300",
  };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${cls[tone]}`}>{children}</span>
  );
}

export function Button({
  children,
  onClick,
  disabled,
  variant = "primary",
  type = "button",
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "danger" | "ghost";
  type?: "button" | "submit";
  className?: string;
}) {
  const v = {
    primary: "bg-blue-600 hover:bg-blue-500 text-white",
    danger: "bg-red-600 hover:bg-red-500 text-white",
    ghost: "border border-gray-700 hover:bg-gray-800 text-gray-200",
  }[variant];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`px-4 py-2 rounded-md text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed ${v} ${className}`}
    >
      {children}
    </button>
  );
}

export function ErrorBox({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return (
    <div className="border border-red-800 bg-red-900/20 text-red-300 rounded-lg p-3 text-sm mb-4 break-words">
      {error}
    </div>
  );
}

export function Loading({ text = "Loading..." }: { text?: string }) {
  return <div className="animate-pulse text-gray-400 py-6">{text}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="text-sm text-gray-500 py-4">{children}</div>;
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
    <details className="border border-gray-800 rounded-md p-3 bg-gray-900/30">
      <summary className="cursor-pointer text-sm text-gray-300">{title}</summary>
      <pre className="whitespace-pre-wrap text-xs text-gray-400 mt-2 font-sans leading-relaxed">
        {text}
      </pre>
    </details>
  );
}
