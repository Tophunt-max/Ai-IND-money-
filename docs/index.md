# SkopaqTrader

**Algorithmic Trading Platform for Indian Equities — one AI: the Adaptive AI**

SkopaqTrader trades NSE stocks and index options with machine-learning models trained on its own market data, signals and trades. There is no LLM and no API AI: every decision comes from models you can retrain, test on unseen data, shadow on paper and promote by hand — behind safety rules no model can change.

---

## What It Does

| Feature | Description |
|---------|-------------|
| **Swing book** | Daily setups (trend pullback, breakout, oversold bounce) ranked by a swing model trained on their net outcomes |
| **Intraday scalper & F&O engine** | Rule strategies, adaptive models (LightGBM, XGBoost, CatBoost, PyTorch …) and RL policies (PPO / A2C / DQN) |
| **Continual learning** | Shadow → champion promotion with gates, rollback, drift checks; a no-code strategy builder |
| **Safety first** | Immutable limits, loss circuit breakers, kill switch, paper by default |
| **Dashboard** | Mobile-first trading UI: charts, positions, signals, swing scanner, AI learning |
| **Telegram & MCP** | Alerts, `/ideas`, `/halt`; 26 MCP tools for Claude Code (data and the Adaptive AI's output) |
| **Docker Ready** | One image — `docker compose up` |

## Quick Start

```bash
git clone https://github.com/Tophunt-max/Ai-IND-money-.git && cd Ai-IND-money-
pip install -e .
cp .env.example .env          # INDstocks token, Supabase (optional), Telegram (optional)
skopaq swing-train            # train the swing model (no orders)
skopaq scan                   # today's swing ideas
skopaq daemon --once --paper  # a full paper session
```

## Architecture at a Glance

```
Swing scan / scalper / F&O engine   (Adaptive AI: setups + models)
  ↓
SignalGate   net R:R after charges, data quality, stop bounds — journalled
  ↓
Executor → PositionSizer → SafetyChecker → OrderRouter
  ↓
INDstocks / Paper Engine → PositionMonitor (stop, target, trailing, EOD)
  ↓
Signal journal → continual learning (shadow models, admin promotion)
```

!!! warning "Disclaimer"
    This software is for **educational and research purposes only**. It is NOT financial advice. Trading involves substantial risk of loss. You are solely responsible for any trades executed.

## Next Steps

- [Installation](getting-started/installation.md) — Set up the platform
- [Quick Start](getting-started/quickstart.md) — Your first paper session
- [Swing Trading](trading/swing-trading.md) — Train, scan, promote
- [Trading Pipeline](architecture/pipeline.md) — How the Adaptive AI decides
- [Telegram Bot](telegram/setup.md) — Alerts and commands on your phone
