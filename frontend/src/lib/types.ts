// Shapes of the dashboard API answers several pages share.

export interface Position {
  symbol: string;
  quantity: number;
  entry_price: number;
  cost: number;
  opened_at: string | null;
  stop_loss: number | null;
  target: number | null;
  ltp?: number | null;
  market_value?: number | null;
  unrealized_pnl?: number | null;
  unrealized_pct?: number | null;
  day_change_pct?: number | null;
}

export interface Overview {
  mode: string;
  kill_switch: { halted: boolean; text: string };
  initial_paper_capital: number;
  positions: Position[];
  realized_pnl: number;
  unrealized_pnl: number | null;
  market_value: number | null;
  closed_trades: number;
  today_trades: number;
  invested: number;
  database: string;
  price_source?: string;
}

export interface PnlHistory {
  days?: number;
  points: { date: string; pnl: number; cumulative: number }[];
  total: number;
}

export interface SwingIdeaDict {
  symbol: string;
  setup: string;
  reason: string;
  entry: number;
  stop: number;
  target: number;
  prob: number | null;
  score: number;
  features?: Record<string, number>;
}

export interface SwingIdeaAnswer {
  symbol: string;
  model: string | null;
  model_state: string;
  threshold: number | null;
  rules_only: boolean;
  idea: SwingIdeaDict | null;
  qualifies: boolean;
}

export const SETUP_LABEL: Record<string, string> = {
  trend_pullback: "Trend pullback", breakout: "Breakout", oversold_bounce: "Oversold bounce",
};
