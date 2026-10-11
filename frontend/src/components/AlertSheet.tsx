"use client";

import { BellPlus } from "lucide-react";
import { useEffect, useState } from "react";

import { api, ApiError, inr } from "@/lib/api";

import { useToast } from "./Toast";
import { Button, Field, Notice, Segmented, Sheet } from "./ui";

export type AlertOp = "above" | "below" | "pct_up" | "pct_down";

export interface PriceAlert {
  id: string; symbol: string; op: AlertOp; value: number; note: string; text: string;
  created_at: string; active: boolean; triggered_at: string | null; triggered_price: number | null;
}

/** How far the price is from a level alert, as a signed % (null for day-change alerts). */
export function alertDistance(a: Pick<PriceAlert, "op" | "value">, ltp: number | null | undefined): number | null {
  if (!ltp || (a.op !== "above" && a.op !== "below")) return null;
  return ((a.value - ltp) / ltp) * 100;
}

const OPS: { value: AlertOp; label: string }[] = [
  { value: "above", label: "Above" }, { value: "below", label: "Below" },
  { value: "pct_up", label: "Up %" }, { value: "pct_down", label: "Down %" },
];

/** Create a price alert (fires once: in-app, in the bell and on Telegram). */
export default function AlertSheet({ open, onClose, symbol: initial, ltp, onSaved }: {
  open: boolean; onClose: () => void; symbol?: string; ltp?: number | null; onSaved?: () => void;
}) {
  const toast = useToast();
  const [symbol, setSymbol] = useState(initial || "");
  const [op, setOp] = useState<AlertOp>("above");
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    setSymbol(initial || "");
    setError(null);
    setNote("");
    setOp("above");
    setValue(ltp ? (ltp * 1.02).toFixed(2) : "");
  }, [open, initial, ltp]);
  useEffect(() => {
    if (!open || !ltp) return;
    if (op === "above") setValue((ltp * 1.02).toFixed(2));
    else if (op === "below") setValue((ltp * 0.98).toFixed(2));
    else setValue("2");
  }, [op, open, ltp]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api("/api/dashboard/alerts", { method: "POST", json: { symbol, op, value: Number(value), note } });
      toast({ tone: "ok", title: `Alert set on ${symbol.toUpperCase()}`, body: "You will get it here, in the bell and on Telegram." });
      onSaved?.();
      onClose();
    } catch (e: any) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const pct = op.startsWith("pct");
  const away = !pct && ltp && Number(value) ? ((Number(value) - ltp) / ltp) * 100 : null;

  return (
    <Sheet open={open} onClose={onClose} title={<span className="flex items-center gap-2"><BellPlus className="h-5 w-5 text-brand-400" /> Price alert</span>}
      footer={<Button size="lg" className="w-full" loading={busy} disabled={!symbol || !(Number(value) > 0)} onClick={save}>Create alert</Button>}>
      <div className="space-y-4 pt-1">
        <Field label="Stock">
          <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="RELIANCE" className="field h-11 uppercase" />
        </Field>
        <Field label="When">
          <Segmented value={op} onChange={setOp} options={OPS} size="sm" />
        </Field>
        <Field label={pct ? "Day change (%)" : "Price (₹)"}
          hint={ltp ? `Now ${inr(ltp)}${away != null ? ` · ${away >= 0 ? "+" : ""}${away.toFixed(2)}% away` : ""}` : undefined}>
          <input inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} className="field num h-11" />
        </Field>
        <Field label="Note (optional)">
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} placeholder="e.g. breakout above resistance" className="field h-11" />
        </Field>
        {error && <Notice tone="error">{error}</Notice>}
        <p className="text-[11px] text-gray-500">Checked on every live price while the dashboard is open, and every minute by the Telegram bot during market hours. Alerts never place orders.</p>
      </div>
    </Sheet>
  );
}
