// Chart studies computed in the browser from the candles on screen (evidence only: the
// engines compute their own on the server). Every function returns one value per candle,
// null until it has enough history.

export interface OHLCV { t: number; o: number; h: number; l: number; c: number; v: number }

export function sma(values: number[], n: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= n) sum -= values[i - n];
    if (i >= n - 1) out[i] = sum / n;
  }
  return out;
}

export function ema(values: number[], n: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length < n) return out;
  const k = 2 / (n + 1);
  let prev = values.slice(0, n).reduce((a, b) => a + b, 0) / n; // seeded with the SMA
  out[n - 1] = prev;
  for (let i = n; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

export function bollinger(values: number[], n = 20, mult = 2) {
  const mid = sma(values, n);
  const upper: (number | null)[] = [];
  const lower: (number | null)[] = [];
  for (let i = 0; i < values.length; i++) {
    const m = mid[i];
    if (m == null) {
      upper.push(null);
      lower.push(null);
      continue;
    }
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) s += (values[j] - m) ** 2;
    const sd = Math.sqrt(s / n);
    upper.push(m + mult * sd);
    lower.push(m - mult * sd);
  }
  return { mid, upper, lower };
}

/** Wilder's RSI. */
export function rsi(values: number[], n = 14): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length <= n) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= n; i++) {
    const d = values[i] - values[i - 1];
    if (d > 0) gain += d; else loss -= d;
  }
  gain /= n;
  loss /= n;
  out[n] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  for (let i = n + 1; i < values.length; i++) {
    const d = values[i] - values[i - 1];
    gain = (gain * (n - 1) + Math.max(d, 0)) / n;
    loss = (loss * (n - 1) + Math.max(-d, 0)) / n;
    out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
  }
  return out;
}

export function macd(values: number[], fast = 12, slow = 26, signal = 9) {
  const f = ema(values, fast);
  const s = ema(values, slow);
  const line = values.map((_, i) => (f[i] != null && s[i] != null ? (f[i] as number) - (s[i] as number) : null));
  const start = line.findIndex((x) => x != null);
  const sig: (number | null)[] = new Array(values.length).fill(null);
  if (start >= 0) {
    const tail = ema(line.slice(start) as number[], signal);
    tail.forEach((x, i) => { sig[start + i] = x; });
  }
  const hist = line.map((x, i) => (x != null && sig[i] != null ? x - (sig[i] as number) : null));
  return { line, signal: sig, hist };
}

/** Session VWAP: typical price × volume, reset at each IST trading day. */
export function vwap(candles: OHLCV[]): (number | null)[] {
  const day = (t: number) => new Date((t + 19800) * 1000).toISOString().slice(0, 10);
  let d = "";
  let pv = 0;
  let vol = 0;
  return candles.map((k) => {
    const kd = day(k.t);
    if (kd !== d) {
      d = kd;
      pv = 0;
      vol = 0;
    }
    const tp = (k.h + k.l + k.c) / 3;
    pv += tp * (k.v || 0);
    vol += k.v || 0;
    return vol > 0 ? pv / vol : null;
  });
}

export const STUDIES = [
  { key: "ema", label: "EMA 9 / 21", pane: "price" },
  { key: "sma", label: "SMA 50 / 200", pane: "price" },
  { key: "bb", label: "Bollinger 20, 2", pane: "price" },
  { key: "vwap", label: "VWAP (intraday)", pane: "price" },
  { key: "volume", label: "Volume", pane: "price" },
  { key: "rsi", label: "RSI 14", pane: "lower" },
  { key: "macd", label: "MACD 12, 26, 9", pane: "lower" },
  { key: "patterns", label: "Candle patterns", pane: "price" },
  { key: "levels", label: "Signal & position levels", pane: "price" },
] as const;
