"""Typer CLI for SkopaqTrader.

Usage examples::

    skopaq token set <token>          # Store INDstocks API token
    skopaq token status               # Check token health
    skopaq status                     # System health overview
    skopaq scan                       # Today's swing ideas (no orders)
    skopaq swing-train                # Train the swing model (Adaptive AI)
    skopaq daemon --once              # One trading session (swing + scalper + F&O)
    skopaq serve                      # Start FastAPI backend
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import typer

from skopaq import __version__
from skopaq.cli.display import (
    display_daemon_report,
    display_daemon_start,
    display_error,
    display_info,
    display_monitor_result,
    display_monitor_start,
    display_report,
    display_serve_banner,
    display_status,
    display_success,
    display_token_health,
    display_token_set,
    display_version,
    display_welcome,
)
from skopaq.cli.theme import console

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="skopaq",
    help="SkopaqTrader — Adaptive-AI algorithmic trading for Indian equities.",
    no_args_is_help=True,
)


@app.callback()
def _apply_dashboard_settings() -> None:
    """Settings saved from the dashboard (~/.skopaq/env_overrides.json) win over the
    environment in every command: api, scheduler, daemon, monitor, ..."""
    from skopaq import env_overrides

    env_overrides.apply()


# ── Token management ─────────────────────────────────────────────────────────

token_app = typer.Typer(help="INDstocks API token management.")
app.add_typer(token_app, name="token")



@token_app.command("set")
def token_set(
    token: str = typer.Argument(..., help="Bearer token from INDstocks dashboard."),
    ttl: float = typer.Option(24.0, help="Token TTL in hours."),
) -> None:
    """Encrypt and store an INDstocks API token."""
    from skopaq.broker.token_manager import TokenManager

    mgr = TokenManager()
    mgr.set_token(token, ttl_hours=ttl)
    health = mgr.get_health()
    display_token_set(health)


@token_app.command("status")
def token_status() -> None:
    """Check current token health."""
    from skopaq.broker.token_manager import TokenManager

    mgr = TokenManager()
    health = mgr.get_health()
    display_token_health(health)

    if not health.valid:
        raise typer.Exit(code=1)


@token_app.command("clear")
def token_clear() -> None:
    """Delete stored token."""
    from skopaq.broker.token_manager import TokenManager

    mgr = TokenManager()
    mgr.clear()
    display_success("Token cleared")


@token_app.command("auto")
def token_auto(
    force: bool = typer.Option(False, "--force",
                               help="Make a new token even if the stored one is still good "
                                    "(it invalidates the previous TOTP token)."),
) -> None:
    """Make today's token from TOTP (SKOPAQ_INDSTOCKS_CLIENT_ID / _MPIN / _TOTP_SECRET).

    Only when the stored token would not last until the end of today's session, unless
    --force. The scheduler does this itself before each session."""
    from datetime import datetime, timedelta, timezone

    from skopaq.broker import auto_token
    from skopaq.config import SkopaqConfig
    from skopaq.execution.readiness import session_end

    config = SkopaqConfig()
    if not auto_token.configured(config):
        console.print("[red]Automatic token is off:[/red] set SKOPAQ_INDSTOCKS_CLIENT_ID, "
                      "SKOPAQ_INDSTOCKS_MPIN and SKOPAQ_INDSTOCKS_TOTP_SECRET "
                      "(docs/deployment/go-live.md)")
        raise typer.Exit(1)
    if force:
        try:
            expires = asyncio.run(auto_token.generate_token(config))
        except auto_token.AutoTokenError as exc:
            console.print(f"[red]Token not made:[/red] {exc}")
            raise typer.Exit(1)
        ist = expires.astimezone(timezone(timedelta(hours=5, minutes=30)))
        display_success(f"New token stored, valid until {ist:%d %b %H:%M} IST")
        return
    result = auto_token.ensure_token_sync(config,
                                          session_end(datetime.now(timezone.utc)))
    if not result.ok:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(1)
    display_success(result.message)


@app.command("preflight")
def preflight(
    live: bool = typer.Option(False, "--live", help="Judge for live trading (real orders)."),
    json_out: bool = typer.Option(False, "--json", help="Print the checks as JSON."),
) -> None:
    """Is this host ready to trade? Token, account, clock, static IP, funds, kill switch,
    websockets, control dir, database. Exit 1 when a check fails."""
    import json as _json

    from skopaq.config import SkopaqConfig
    from skopaq.execution.readiness import check_readiness

    config = SkopaqConfig()
    result = asyncio.run(check_readiness(config, live=True if live else None))
    if json_out:
        console.print_json(_json.dumps(result.as_dict()))
    else:
        icons = {"ok": "[green]✔[/green]", "warn": "[yellow]![/yellow]", "fail": "[red]✘[/red]"}
        console.print(f"[bold]Readiness ({'LIVE' if result.live else 'paper'})[/bold] — "
                      f"{result.checked_at}")
        for c in result.checks:
            console.print(f" {icons.get(c.status, '?')} [bold]{c.name}[/bold]: {c.detail}")
            if c.fix and c.status != "ok":
                console.print(f"     → {c.fix}")
        console.print("[green]READY[/green]" if result.passed else "[red]NOT READY[/red]")
    raise typer.Exit(0 if result.passed else 1)


# ── Status ───────────────────────────────────────────────────────────────────


@app.command("status")
def status() -> None:
    """Show system health overview."""
    from skopaq.broker.token_manager import TokenManager
    from skopaq.config import SkopaqConfig

    config = SkopaqConfig()
    mgr = TokenManager()
    health = mgr.get_health()

    from skopaq.execution import kill_switch

    display_welcome()
    display_status(__version__, config, health, halt=kill_switch.status())


@app.command("ticks")
def ticks(
    symbols: list[str] = typer.Argument(..., help="NSE symbols, e.g. RELIANCE TCS"),
    seconds: int = typer.Option(60, help="How long to listen (seconds)."),
    interval: int = typer.Option(60, help="Candle size in seconds."),
    orders: bool = typer.Option(False, "--orders", help="Also print order updates."),
) -> None:
    """Watch live INDstocks WebSocket ticks, with candles and indicators (read only)."""
    from skopaq.config import SkopaqConfig

    _setup_logging("WARNING")
    raise typer.Exit(asyncio.run(_run_ticks(SkopaqConfig(), symbols, seconds, interval,
                                            orders)))


async def _run_ticks(config, symbols: list[str], seconds: int, interval: int,
                     orders: bool) -> int:
    """Subscribe ``symbols``, print each tick and closed candle; returns 1 when no tick
    arrived (the feed did not work), else 0."""
    from datetime import datetime, timezone

    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.scrip_resolver import resolve_scrip_code
    from skopaq.broker.token_manager import TokenManager
    from skopaq.broker.websocket import OrderUpdateFeed, PriceFeed
    from skopaq.market.candles import LiveSeries

    tokens = TokenManager()
    async with INDstocksClient(config, tokens) as client:
        codes = {await resolve_scrip_code(client, s.upper()): s.upper() for s in symbols}
    feed = PriceFeed(config.indstocks_ws_price_url, tokens)
    series = {code: LiveSeries(interval) for code in codes}

    def on_tick(tick) -> None:
        name = codes.get(tick.scrip_code, tick.instrument)
        ts = tick.timestamp or datetime.now(timezone.utc)
        closed = series[tick.scrip_code].add(tick.ltp, ts, tick.volume) \
            if tick.scrip_code in series else None
        print(f"{ts.astimezone().strftime('%H:%M:%S')}  {name:<12} {tick.ltp:>10.2f}")
        if closed is not None:
            ind = series[tick.scrip_code].indicators.as_dict()
            print(f"  candle {closed.start.strftime('%H:%M')} O {closed.open} H {closed.high} "
                  f"L {closed.low} C {closed.close} | {ind}")

    feed.add_listener(on_tick)
    order_feed = None
    if orders:
        order_feed = OrderUpdateFeed(config.indstocks_ws_order_url, tokens)
        order_feed.add_listener(lambda u: print(
            f"ORDER {u.order_id} {u.side} {u.entity_name} {u.status} "
            f"price={u.executed_price} {u.error_message}"))
        await order_feed.start()
    await feed.subscribe(list(codes))
    await feed.start()
    try:
        await asyncio.sleep(seconds)
    finally:
        await feed.stop()
        if order_feed is not None:
            await order_feed.stop()
    print(f"{feed.ticks} tick(s), {feed.connects} connection(s)")
    return 0 if feed.ticks else 1


@app.command("scalp")
def scalp(
    live: bool = typer.Option(False, "--live", help="Real INTRADAY orders on INDstocks."),
    confirm_live: bool = typer.Option(False, "--confirm-live", help="Skip the live prompt."),
    symbols: str = typer.Option("", help="Comma-separated symbols (default: SCALP_SYMBOLS)."),
    strategies: str = typer.Option("", help="vwap_pullback,ema_rsi,orb,range_reversal"),
) -> None:
    """Run the intraday scalper now, until its flatten time (paper unless --live)."""
    from skopaq.config import SkopaqConfig

    _setup_logging("INFO")
    config = SkopaqConfig()
    config.trading_mode = "live" if live else "paper"
    if symbols:
        config.scalp_symbols = symbols
    if strategies:
        config.scalp_strategies = strategies
    if live and not confirm_live and not typer.confirm(
            "LIVE scalping: real INTRADAY orders with real money. Proceed?"):
        raise typer.Exit(1)
    report = asyncio.run(_run_scalp(config))
    console.print(report.summary())
    raise typer.Exit(4 if report.left_open else 0)


async def _run_scalp(config):
    import signal as sig
    import time as _time

    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.paper_engine import PaperEngine
    from skopaq.broker.token_manager import TokenManager
    from skopaq.broker.websocket import feeds_from_config
    from skopaq.constants import DAEMON_PAPER_SAFETY_RULES, DAEMON_SAFETY_RULES
    from skopaq.execution.control import ControlChannel, watch_stop
    from skopaq.execution.executor import Executor
    from skopaq.execution.order_router import OrderRouter
    from skopaq.execution.pnl_history import seed_safety_checker
    from skopaq.execution.safety_checker import SafetyChecker
    from skopaq.scalping.engine import ScalpEngine

    live = config.trading_mode == "live"
    tokens = TokenManager()
    client = INDstocksClient(config, tokens)
    router = OrderRouter(config, PaperEngine(initial_capital=config.initial_paper_capital),
                         live_client=client if live else None)
    rules = DAEMON_SAFETY_RULES if live else DAEMON_PAPER_SAFETY_RULES
    safety = SafetyChecker(rules=rules,
                           max_sector_concentration_pct=config.max_sector_concentration_pct)
    seed_safety_checker(safety, config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (sig.SIGINT, sig.SIGTERM):
        loop.add_signal_handler(s, stop.set)
    control = ControlChannel.from_config(config)
    watcher = loop.create_task(watch_stop(control, stop, started_at=_time.time()))
    feed, _ = feeds_from_config(config, tokens)
    async with client:
        if feed is not None:
            await feed.start()
        engine = ScalpEngine(
            config, Executor(router, safety), client, router, feed=feed, control=control,
            on_trade=lambda signal, execution: _record_exit(config, signal,
                                                            execution),
            max_qty=safety.max_shares)
        try:
            report = await engine.run(stop)
        finally:
            watcher.cancel()
            if feed is not None:
                await feed.stop()
            if router.worker is not None:
                await router.registry.drain_recordings()
    return report


@app.command("fno")
def fno_trade(
    live: bool = typer.Option(False, "--live", help="Real F&O orders on INDstocks."),
    confirm_live: bool = typer.Option(False, "--confirm-live", help="Skip the live prompt."),
    underlyings: str = typer.Option("", help="Comma-separated, e.g. NIFTY,BANKNIFTY "
                                             "(default: FNO_UNDERLYINGS)."),
    instrument: str = typer.Option("", help="options (CE/PE buying) or futures (long only)."),
    strategies: str = typer.Option("", help="vwap_pullback,ema_rsi,orb,range_reversal"),
) -> None:
    """Run the F&O engine now (options buying), until its flatten time (paper unless
    --live). It never writes options nor shorts futures."""
    from skopaq.config import SkopaqConfig

    _setup_logging("INFO")
    config = SkopaqConfig()
    config.trading_mode = "live" if live else "paper"
    if underlyings:
        config.fno_underlyings = underlyings
    if instrument:
        config.fno_instrument = instrument
    if strategies:
        config.fno_strategies = strategies
    if live and not confirm_live and not typer.confirm(
            "LIVE F&O: real option/future BUY orders with real money. Proceed?"):
        raise typer.Exit(1)
    report = asyncio.run(_run_fno(config))
    console.print(report.summary())
    raise typer.Exit(4 if report.left_open else 0)


async def _run_fno(config):
    import signal as sig
    import time as _time

    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.paper_engine import PaperEngine
    from skopaq.broker.token_manager import TokenManager
    from skopaq.broker.websocket import feeds_from_config
    from skopaq.constants import DAEMON_PAPER_SAFETY_RULES, DAEMON_SAFETY_RULES
    from skopaq.execution.control import ControlChannel, watch_stop
    from skopaq.execution.executor import Executor
    from skopaq.execution.order_router import OrderRouter
    from skopaq.execution.pnl_history import seed_safety_checker
    from skopaq.execution.safety_checker import SafetyChecker
    from skopaq.scalping.fno_engine import FnoEngine

    live = config.trading_mode == "live"
    tokens = TokenManager()
    client = INDstocksClient(config, tokens)
    router = OrderRouter(config, PaperEngine(initial_capital=config.initial_paper_capital),
                         live_client=client if live else None)
    rules = DAEMON_SAFETY_RULES if live else DAEMON_PAPER_SAFETY_RULES
    safety = SafetyChecker(rules=rules,
                           max_sector_concentration_pct=config.max_sector_concentration_pct)
    seed_safety_checker(safety, config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (sig.SIGINT, sig.SIGTERM):
        loop.add_signal_handler(s, stop.set)
    control = ControlChannel.from_config(config)
    watcher = loop.create_task(watch_stop(control, stop, started_at=_time.time()))
    feed, _ = feeds_from_config(config, tokens)
    async with client:
        if feed is not None:
            await feed.start()
        engine = FnoEngine(
            config, Executor(router, safety), client, router, feed=feed, control=control,
            on_trade=lambda signal, execution: _record_exit(config, signal,
                                                            execution),
            max_lots=safety.max_lots, max_position_pct=rules.max_position_pct)
        try:
            report = await engine.run(stop)
        finally:
            watcher.cancel()
            if feed is not None:
                await feed.stop()
            if router.worker is not None:
                await router.registry.drain_recordings()
    return report


@app.command("scalp-backtest")
def scalp_backtest(
    symbol: str = typer.Argument(..., help="NSE symbol, e.g. RELIANCE"),
    days: int = typer.Option(5, help="Trading days of 1-minute candles (INDstocks)."),
    strategies: str = typer.Option("", help="vwap_pullback,ema_rsi,orb,range_reversal"),
    max_qty: int = typer.Option(0, help="Share cap per scalp (0 = none; the safety rules "
                                        "allow SKOPAQ_MAX_SHARES_PER_ORDER)."),
    equity: float = typer.Option(1_000_000.0, help="Equity the risk per trade is taken of."),
) -> None:
    """Backtest the scalping strategies on past 1-minute candles (no orders)."""
    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.token_manager import TokenManager
    from skopaq.config import SkopaqConfig
    from skopaq.scalping.backtest import fetch_days, simulate
    from skopaq.scalping.settings import ScalpSettings

    _setup_logging("WARNING")
    config = SkopaqConfig()
    if strategies:
        config.scalp_strategies = strategies
    settings = ScalpSettings.from_config(config)

    async def load():
        async with INDstocksClient(config, TokenManager()) as client:
            return await fetch_days(client, symbol.upper(), days)

    candles = asyncio.run(load())
    if not candles:
        console.print(f"No 1-minute candles for {symbol.upper()}")
        raise typer.Exit(1)
    result = simulate(symbol.upper(), candles, settings, equity=equity,
                      max_qty=max_qty or None)
    console.print(f"{symbol.upper()}: {len(candles)} day(s), strategies "
                  f"{', '.join(settings.strategies)} (skipped for charges: "
                  f"{result.skipped_cost})")
    for name, row in result.stats().items():
        console.print(f"  {name:<15} trades {row['trades']:>3}  win {row['win_rate_pct']:>5}%  "
                      f"net ₹{row['net_pnl']:>10,.2f}  PF {row['profit_factor']}  "
                      f"max DD ₹{row['max_drawdown']:,.2f}")
    for t in result.trades[-20:]:
        console.print(f"  {t.day} {t.opened}-{t.closed} {t.strategy:<14} {t.qty:>5} "
                      f"{t.entry:>9.2f} → {t.exit:>9.2f}  ₹{t.pnl:>9.2f}  {t.reason}")


@app.command("learn-train")
def learn_train(
    symbols: str = typer.Option("", help="Comma-separated NSE symbols to backtest for "
                                         "training data (INDstocks 1-minute candles)."),
    days: int = typer.Option(20, help="Trading days of candles per symbol."),
    journal: bool = typer.Option(False, help="Also use the paper/live signal journal's "
                                             "outcomes."),
    slippage_pct: float = typer.Option(0.0002, help="Adverse slippage per side assumed."),
    min_examples: int = typer.Option(200, help="Refuse to train on fewer labelled rows."),
    model: str = typer.Option("logistic", help="logistic (NumPy) or lightgbm (pip install "
                                               "skopaqtrader[ml])."),
    regimes: bool = typer.Option(False, help="Also build a candidate regime → strategy map "
                                             "from the held-out trades (approve it on the "
                                             "dashboard)."),
) -> None:
    """Train a candidate model on point-in-time features (no orders).

    Chronological train/validation/test split, the threshold chosen on validation, the
    report on the unseen test period; the candidate is registered and gated. Passing the
    gates only makes it a *shadow* model: promotion needs the shadow period and an admin
    (dashboard → Learning)."""
    from skopaq.config import SkopaqConfig
    from skopaq.learning.dataset import from_backtest, from_journal
    from skopaq.learning.pipeline import InsufficientData, train_candidate
    from skopaq.learning.registry import ModelRegistry, model_dir
    from skopaq.scalping.settings import ScalpSettings
    from skopaq.strategy.journal import SignalJournal

    _setup_logging("WARNING")
    config = SkopaqConfig()
    settings = ScalpSettings.from_config(config)
    examples, leakage = [], []
    names = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if names:
        from skopaq.broker.client import INDstocksClient
        from skopaq.broker.token_manager import TokenManager
        from skopaq.scalping.backtest import fetch_days

        async def load(sym):
            async with INDstocksClient(config, TokenManager()) as client:
                return await fetch_days(client, sym, days)

        for sym in names:
            candles = asyncio.run(load(sym))
            rep = from_backtest(sym, candles, settings, slippage_pct=slippage_pct)
            examples += rep.examples
            leakage += rep.leakage
            console.print(f"  {sym}: {len(candles)} day(s), {len(rep.examples)} trade rows")
    if journal:
        rep = from_journal(SignalJournal.from_config(config).signals())
        examples += rep.examples
        leakage += rep.leakage
        console.print(f"  journal: {len(rep.labelled)} labelled, {rep.unlabelled} without an "
                      f"outcome, {rep.rejected_signals} rejected signals")
    if not examples:
        console.print("No data: pass --symbols and/or --journal")
        raise typer.Exit(1)
    registry = ModelRegistry(model_dir(config))
    from skopaq.db.mirror import mirror_from_config

    registry.mirror = mirror_from_config(config)
    try:
        entry = train_candidate(examples, strategy_id="scalp.gate", registry=registry,
                                min_examples=min_examples, leakage=leakage, model_kind=model)
    except InsufficientData as exc:
        console.print(f"Not trained: {exc}")
        raise typer.Exit(2)
    rep = entry["report"]
    console.print(f"Candidate {entry['version']} — state {entry['state']}")
    console.print(f"  test samples {rep['samples']}, threshold {rep['threshold']}")
    console.print(f"  baseline (every setup): {rep['baseline']}")
    console.print(f"  model gate:             {rep.get('model')}")
    console.print(f"  Brier {rep.get('brier')} vs base rate {rep.get('brier_base_rate')}")
    for fail in entry.get("gate_failures") or []:
        console.print(f"  [yellow]gate:[/yellow] {fail}")
    if regimes:
        from skopaq.learning.dataset import chronological_split
        from skopaq.strategy.selector import RegimeMapStore, build_regime_map

        _, _, test = chronological_split(examples)       # held-out trades only
        regime_map = build_regime_map(test)
        RegimeMapStore(model_dir(config)).save_candidate(
            regime_map, dataset={"test_trades": len(test)})
        for regime, rows in regime_map["regimes"].items():
            ok = [s for s, r in rows.items() if r["allowed"]]
            console.print(f"  regime {regime:<14} validated: {', '.join(ok) or 'none (idle)'}")
        console.print("  Candidate regime map saved: approve it on the dashboard to use it")


def _fetch_history(config, symbols: list[str], days: int) -> dict:
    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.token_manager import TokenManager
    from skopaq.scalping.backtest import fetch_days

    async def load():
        out = {}
        async with INDstocksClient(config, TokenManager()) as client:
            for sym in symbols:
                out[sym] = await fetch_days(client, sym, days)
        return out

    return asyncio.run(load())


@app.command("adaptive-train")
def adaptive_train(
    symbols: str = typer.Option("", help="Symbols (default SKOPAQ_ADAPTIVE_SYMBOLS, else "
                                         "the scalp symbols)."),
    strategy: str = typer.Option("", help="Your strategy: a saved no-code strategy's name, "
                                          "spec.json, or path/to/file.py:ClassName (default: "
                                          "the dashboard's selection / .env / built-in)."),
    correlated: str = typer.Option("", help="Correlated symbols whose features are added."),
    days: int = typer.Option(0, help="Days of 1-minute history (default SKOPAQ_ADAPTIVE_DAYS)."),
    model: str = typer.Option("", help="lightgbm | xgboost | xgboost_rf | catboost | "
                                       "random_forest | extra_trees | linear | mlp | "
                                       "ft_transformer."),
    scaler: str = typer.Option("", help="minmax | standard | robust | none."),
    pca: Optional[bool] = typer.Option(None, "--pca/--no-pca", help="PCA (99.9 % variance)."),
    svm: Optional[bool] = typer.Option(None, "--svm/--no-svm", help="One-class SVM outliers."),
    dbscan: Optional[bool] = typer.Option(None, "--dbscan/--no-dbscan",
                                          help="DBSCAN outliers."),
    di: Optional[float] = typer.Option(None, help="Dissimilarity Index threshold (0 = off)."),
    noise: Optional[float] = typer.Option(None, help="Training noise std (0 = off)."),
    weight_factor: Optional[float] = typer.Option(None, help="Recent-row weighting "
                                                             "(0 = off, e.g. 0.9)."),
    continual: Optional[bool] = typer.Option(None, "--continual/--fresh",
                                             help="Continue the previous model (default "
                                                  "SKOPAQ_ADAPTIVE_CONTINUAL_LEARNING)."),
) -> None:
    """Train your adaptive strategy (no orders): look-ahead check, data pipeline, model per
    target, rolling retrain backtest; saved as a verified bundle, registered as a
    candidate and gated (passing = shadow; promotion stays an admin's decision)."""
    from dataclasses import replace

    from skopaq.adaptive.framework import config_with, train_bundle
    from skopaq.adaptive.retrain import (
        purge_bundles,
        strategy_from,
        symbols_from,
        train_config_from,
    )
    from skopaq.adaptive.strategy import LookAheadError, load_strategy
    from skopaq.config import SkopaqConfig
    from skopaq.db.mirror import mirror_from_config
    from skopaq.learning.registry import ModelRegistry, model_dir

    _setup_logging("WARNING")
    config = SkopaqConfig()
    from skopaq.adaptive.spec import SpecError, SpecStrategy, StrategyStore

    try:
        if strategy and ":" not in strategy and not strategy.endswith(".json"):
            saved = StrategyStore(model_dir(config)).get(strategy)
            if saved is None:
                raise SpecError(f"no saved strategy {strategy!r}")
            strat = SpecStrategy(saved["spec"])
        else:
            strat = load_strategy(strategy) if strategy else strategy_from(config)
    except (SpecError, OSError, ValueError) as exc:
        console.print(f"[red]Strategy not loaded:[/red] {exc}")
        raise typer.Exit(2)
    if correlated:
        strat.correlated = tuple(x.strip().upper() for x in correlated.split(",") if x.strip())
    cfg = config_with(train_config_from(config), scaler=scaler or None, pca=pca, svm=svm,
                      dbscan=dbscan, di_threshold=di, noise_std=noise)
    if model:
        cfg = replace(cfg, model_kind=model)
    if weight_factor is not None:
        cfg = replace(cfg, weight_factor=weight_factor)
    if continual is not None:
        cfg = replace(cfg, continual=continual)
    names = [s.strip().upper() for s in symbols.split(",") if s.strip()] or symbols_from(config)
    n_days = days or int(config.adaptive_days)
    hist = _fetch_history(config, names, n_days)
    corr = _fetch_history(config, list(strat.correlated), n_days) if strat.correlated else {}
    registry = ModelRegistry(model_dir(config))
    registry.mirror = mirror_from_config(config)
    try:
        entry = train_bundle(strat, hist, cfg, correlated=corr, registry=registry)
    except LookAheadError as exc:
        console.print(f"[red]Refused: {exc}[/red]")
        raise typer.Exit(2)
    except (ValueError, ImportError) as exc:
        console.print(f"Not trained: {exc}")
        raise typer.Exit(2)
    purge_bundles(registry, int(config.adaptive_keep_models))
    rep = entry["report"]
    console.print(f"Candidate {entry['version']} ({cfg.model_kind}, strategy {strat.name}, "
                  f"{rep.get('features')} features, targets {rep.get('targets')}) — "
                  f"state {entry['state']}")
    console.print(f"  pipeline: {rep.get('pipeline')}")
    console.print(f"  entry rule: {rep.get('rule')}")
    console.print(f"  test: every row {rep['baseline']}")
    console.print(f"        model     {rep.get('model')}")
    console.print(f"        model, no outliers {rep.get('model_di')} ({rep.get('outliers')})")
    roll = rep.get("rolling") or {}
    console.print(f"  rolling retrain: {roll.get('retrains', 0)}×; every row "
                  f"{roll.get('baseline', {}).get('net')}, "
                  f"model {roll.get('model', {}).get('net')}")
    console.print(f"  top features: {[n for n, _ in rep.get('importance', [])[:5]]}")
    cont = rep.get("continual") or {}
    if cont.get("enabled"):
        how = (
            f"continued from {cont['parent']} {cont.get('models', '')}"
            if cont.get("parent")
            else f"fresh — {cont.get('fresh')}"
        )
        console.print(f"  continual: {how}")
    for fail in entry.get("gate_failures") or []:
        console.print(f"  [yellow]gate:[/yellow] {fail}")


@app.command("model-export")
def model_export(
    version: str = typer.Argument(..., help="Registry version."),
    file: str = typer.Argument(..., help="Target .tar.gz."),
) -> None:
    """Export a verified model bundle (to move it to another host)."""
    from pathlib import Path

    from skopaq.adaptive import artifacts
    from skopaq.config import SkopaqConfig
    from skopaq.learning.registry import ModelRegistry, model_dir

    reg = ModelRegistry(model_dir(SkopaqConfig()))
    entry = reg.get(version)
    bundle = (entry or {}).get("model", {}).get("bundle")
    if not bundle:
        console.print("Not a bundle model")
        raise typer.Exit(1)
    artifacts.export_bundle(Path(reg.dir) / "artifacts" / bundle, Path(file))
    (Path(file).with_suffix(".entry.json")).write_text(__import__("json").dumps(entry, indent=1))
    console.print(f"Exported {bundle} → {file} (+ registry entry)")


@app.command("model-import")
def model_import(
    file: str = typer.Argument(..., help="A bundle .tar.gz from model-export."),
) -> None:
    """Import a bundle as a new *candidate* (it is gated again here, never champion)."""
    import json
    from pathlib import Path

    from skopaq.adaptive import artifacts
    from skopaq.config import SkopaqConfig
    from skopaq.learning.registry import ModelRegistry, model_dir

    reg = ModelRegistry(model_dir(SkopaqConfig()))
    dest = artifacts.import_bundle(Path(file), Path(reg.dir) / "artifacts")
    manifest = artifacts.verify(dest)
    report = json.loads((dest / "report.json").read_text())
    entry = reg.register(
        model={
            "kind": "adaptive-bundle-v1",
            "bundle": dest.name,
            "path": str(dest),
            "manifest_sha": manifest["files"]["bundle.json"]["sha256"],
        },
        report=report,
        params={},
        dataset=manifest.get("dataset") or {},
        strategy_id="adaptive.imported",
        notes=f"imported from {Path(file).name}",
    )
    fails = reg.evaluate_candidate(entry["version"])
    console.print(f"Imported {dest.name} as {entry['version']}; gates: {fails or 'passed'}")


@app.command("model-verify")
def model_verify() -> None:
    """Check every stored bundle against its manifest (exit 1 on a mismatch)."""
    from pathlib import Path

    from skopaq.adaptive import artifacts
    from skopaq.config import SkopaqConfig
    from skopaq.learning.registry import model_dir

    root = Path(model_dir(SkopaqConfig())) / "artifacts"
    bad = 0
    for d in sorted(root.glob("*")) if root.is_dir() else []:
        try:
            m = artifacts.verify(d)
            console.print(f"  ok   {d.name} ({len(m['files'])} files)")
        except artifacts.IntegrityError as exc:
            bad += 1
            console.print(f"  [red]FAIL[/red] {exc}")
    if bad:
        raise typer.Exit(1)


@app.command("hyperopt")
def hyperopt_cmd(
    symbols: str = typer.Option("", help="Symbols (default the scalp symbols)."),
    days: int = typer.Option(30, help="Days of 1-minute history."),
    trials: int = typer.Option(60, help="Optuna trials."),
) -> None:
    """Search the scalper's strategy parameters (Optuna) on 70 % of the days, prove them on
    the other 30 %; saves a candidate an admin approves on the dashboard (no orders)."""
    from skopaq.adaptive.hyperopt import ParamsStore, hyperopt
    from skopaq.adaptive.retrain import symbols_from
    from skopaq.config import SkopaqConfig
    from skopaq.learning.registry import model_dir
    from skopaq.scalping.settings import ScalpSettings

    _setup_logging("WARNING")
    config = SkopaqConfig()
    names = [s.strip().upper() for s in symbols.split(",") if s.strip()] or symbols_from(config)
    res = hyperopt(
        _fetch_history(config, names, days), ScalpSettings.from_config(config), trials=trials
    )
    entry = ParamsStore(model_dir(config)).save_candidate(res)
    console.print(f"Candidate {entry['version']}: {res['best_params']}")
    console.print(f"  holdout: current {res['holdout_current']} vs best {res['holdout_best']}")
    console.print(
        "  better on the holdout: approve it on the dashboard"
        if res["better_on_holdout"]
        else "  [yellow]not better on the holdout: it cannot be approved[/yellow]"
    )


@app.command("rl-research")
def rl_research(
    symbol: str = typer.Argument(..., help="NSE symbol."),
    days: int = typer.Option(30, help="Days of 1-minute history."),
    epochs: int = typer.Option(5, help="Training passes over the training days."),
) -> None:
    """Offline reinforcement-learning research (tabular Q-learning, net of charges). Never
    trades: a result here is a hypothesis for a rule strategy, not a strategy."""
    from skopaq.adaptive.rl import research_run
    from skopaq.config import SkopaqConfig

    _setup_logging("WARNING")
    config = SkopaqConfig()
    hist = _fetch_history(config, [symbol.upper()], days)
    console.print(research_run(hist[symbol.upper()], epochs=epochs))


@app.command("rl-train")
def rl_train(
    symbols: str = typer.Option(
        "", help="Symbols (default SKOPAQ_RL_SYMBOLS, else the adaptive / scalp symbols)."
    ),
    algo: str = typer.Option("", help="ppo | a2c | dqn (default SKOPAQ_RL_ALGO)."),
    days: int = typer.Option(0, help="Days of 1-minute history (default SKOPAQ_RL_DAYS)."),
    timesteps: int = typer.Option(0, help="Training steps (default SKOPAQ_RL_TIMESTEPS)."),
    seed: int = typer.Option(7, help="Random seed (reproducible runs)."),
    continual: Optional[bool] = typer.Option(
        None,
        "--continual/--fresh",
        help="Continue the previous policy (default SKOPAQ_RL_CONTINUAL_LEARNING).",
    ),
) -> None:
    """Train a PPO / A2C / DQN trading policy in the scalper's own rules (net of charges),
    test it on later unseen days and register it as an RL candidate. Passing the gates
    makes it the shadow policy (paper trading only); live needs an admin's promotion."""
    from skopaq.adaptive.rl_agents import (
        ALGOS,
        config_from,
        rl_registry,
        symbols_from,
        torch_available,
        train_and_register,
    )
    from skopaq.config import SkopaqConfig
    from skopaq.db.mirror import mirror_from_config
    from skopaq.learning.registry import model_dir

    _setup_logging("WARNING")
    config = SkopaqConfig()
    if not torch_available():
        console.print("RL agents need PyTorch: pip install 'skopaq[torch]'")
        raise typer.Exit(2)
    cfg = config_from(
        config, algo=algo or None, timesteps=timesteps or None, seed=seed, continual=continual
    )
    if cfg.algo not in ALGOS:
        console.print(f"Unknown algorithm {cfg.algo!r}: one of {', '.join(ALGOS)}")
        raise typer.Exit(2)
    names = [s.strip().upper() for s in symbols.split(",") if s.strip()] or symbols_from(config)
    hist = _fetch_history(config, names, days or int(config.rl_days))
    registry = rl_registry(model_dir(config))
    registry.mirror = mirror_from_config(config)
    try:
        out = train_and_register(hist, cfg, registry=registry)
    except ValueError as exc:
        console.print(f"Not trained: {exc}")
        raise typer.Exit(2)
    rep = out["report"]
    console.print(
        f"RL {cfg.algo.upper()} {out['version']} — state {out['state']} "
        f"({rep['train_sessions']} training / {rep['test_sessions']} test sessions, "
        f"{rep['seconds']} s)"
    )
    console.print(f"  test (unseen days): {rep['model']}")
    console.print(f"  baseline ({rep['baseline_name']}): {rep['baseline']}")
    console.print(f"  exits {rep['exits']}  actions {rep['actions']}")
    cont = rep.get("continual") or {}
    if cont.get("enabled"):
        console.print(
            "  continual: "
            + (
                f"continued from {cont['parent']}"
                if cont.get("parent")
                else f"fresh — {cont.get('fresh')}"
            )
        )
    for fail in out.get("gate_failures") or []:
        console.print(f"  [yellow]gate:[/yellow] {fail}")


@app.command("rl-status")
def rl_status() -> None:
    """The RL registry: champion, shadow and recent policies."""
    from skopaq.adaptive.rl_agents import rl_registry
    from skopaq.config import SkopaqConfig
    from skopaq.learning.registry import model_dir

    config = SkopaqConfig()
    reg = rl_registry(model_dir(config))
    champ, shadow = reg.champion(), reg.shadow()
    console.print(
        f"enabled={config.rl_enabled} allow_live={config.rl_allow_live} "
        f"paper_use_shadow={config.rl_paper_use_shadow}"
    )
    console.print(f"champion: {champ['version'] if champ else None}")
    console.print(f"shadow:   {shadow['version'] if shadow else None}")
    for v in reg.versions()[-10:]:
        m = (v.get("report") or {}).get("model") or {}
        console.print(
            f"  {v['version']}  {v['state']:<9} net {m.get('net')} trades "
            f"{m.get('trades')} PF {m.get('profit_factor')}"
        )


@app.command("rl-promote")
def rl_promote(
    version: str = typer.Argument(..., help="The shadow policy's version."),
    approved_by: str = typer.Option(..., "--by", help="Your name (recorded)."),
    reason: str = typer.Option(..., help="Why (recorded)."),
) -> None:
    """Shadow → champion after the shadow days (the only way an RL policy can trade live)."""
    from skopaq.adaptive.rl_agents import RL_GATES, rl_registry
    from skopaq.config import SkopaqConfig
    from skopaq.learning.registry import model_dir

    reg = rl_registry(model_dir(SkopaqConfig()))
    try:
        reg.promote(version, approved_by=approved_by, reason=reason, gates=RL_GATES)
    except (KeyError, ValueError) as exc:
        console.print(f"[red]Not promoted:[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"{version} is the RL champion (previous one kept for rollback)")


@app.command("rl-rollback")
def rl_rollback(
    by: str = typer.Option(..., help="Your name (recorded)."),
    reason: str = typer.Option(..., help="Why (recorded)."),
) -> None:
    """Make the previous RL champion the champion again (none: no RL champion)."""
    from skopaq.adaptive.rl_agents import rl_registry
    from skopaq.config import SkopaqConfig
    from skopaq.learning.registry import model_dir

    prev = rl_registry(model_dir(SkopaqConfig())).rollback(by=by, reason=reason)
    console.print(f"RL champion is now {prev}")


def _swing_symbols(config, symbols: str) -> list[str]:
    raw = symbols or getattr(config, "swing_symbols", "") or ""
    names = [x.strip().upper() for x in str(raw).split(",") if x.strip()]
    if names:
        return names
    from skopaq.scanner.watchlist import NIFTY_50

    return list(NIFTY_50)


@app.command("scan")
def scan(
    symbols: str = typer.Option("", help="Symbols (default SKOPAQ_SWING_SYMBOLS, else NIFTY 50)."),
    limit: int = typer.Option(10, help="Ideas shown."),
) -> None:
    """Today's swing ideas: daily setups ranked by the swing model (no orders)."""
    from rich.table import Table

    from skopaq.config import SkopaqConfig
    from skopaq.swing.engine import SwingEngine

    _setup_logging("WARNING")
    config = SkopaqConfig()
    engine = SwingEngine.from_config(config, paper=config.trading_mode != "live")
    ideas = asyncio.run(engine.scan(_swing_symbols(config, symbols), limit))
    console.print(
        f"Swing model: {getattr(engine.model, 'version', None) or 'none'} "
        f"({engine.model_state}){' · rules only' if engine.rules_only else ''}"
    )
    if not ideas:
        console.print(
            "No qualifying setup today."
            if engine.model or engine.rules_only
            else "No swing model yet: run `skopaq swing-train` (paper: "
            "SKOPAQ_SWING_RULES_ONLY=true ranks the setups without one)."
        )
        return
    table = Table(header_style="bold magenta")
    for col in ("Symbol", "Setup", "Entry", "Stop", "Target", "P(win)", "Why"):
        table.add_column(col)
    for i in ideas:
        table.add_row(
            i.symbol,
            i.setup.name,
            f"{i.setup.entry:.2f}",
            f"{i.setup.stop:.2f}",
            f"{i.setup.target:.2f}",
            "—" if i.prob is None else f"{i.prob:.2f}",
            i.setup.reason,
        )
    console.print(table)


@app.command("swing-train")
def swing_train(
    symbols: str = typer.Option("", help="Symbols (default SKOPAQ_SWING_SYMBOLS, else NIFTY 50)."),
    days: int = typer.Option(
        0, help="Calendar days of daily history (default SKOPAQ_SWING_TRAIN_DAYS)."
    ),
    model: str = typer.Option(
        "",
        help="lightgbm | xgboost | catboost | random_forest | "
        "extra_trees | linear | mlp (default SKOPAQ_SWING_MODEL_KIND).",
    ),
) -> None:
    """Train the swing model on years of daily setups (net of delivery charges), test it
    on the latest unseen dates and register it as a candidate (gates passed: shadow)."""
    from skopaq.config import SkopaqConfig
    from skopaq.learning.registry import model_dir
    from skopaq.swing.data import history
    from skopaq.swing.engine import params_from
    from skopaq.swing.model import SwingTrainConfig, swing_registry, train_and_register

    _setup_logging("WARNING")
    config = SkopaqConfig()
    n_days = days or int(config.swing_train_days)
    hist = {}
    for sym in _swing_symbols(config, symbols):
        try:
            hist[sym] = history(sym, n_days)
        except Exception as exc:
            console.print(f"[yellow]{sym}: no history ({exc})[/yellow]")
    cfg = SwingTrainConfig(model_kind=model or config.swing_model_kind, params=params_from(config))
    registry = swing_registry(model_dir(config))
    try:
        out = train_and_register(hist, cfg, registry=registry)
    except (ValueError, ImportError) as exc:
        console.print(f"Not trained: {exc}")
        raise typer.Exit(2)
    rep = out["report"]
    console.print(f"Swing model {out['version']} ({cfg.model_kind}) — state {out['state']}")
    console.print(
        f"  {rep['setups']} setups on {len(hist)} symbols · test {rep['first_test_date']}"
        f" → {rep['last_test_date']} · threshold P ≥ {rep['threshold']}"
    )
    console.print(f"  test, every setup: {rep['baseline']}")
    console.print(f"  test, model's:     {rep['model']}")
    console.print(f"  Brier {rep['brier']:.4f} vs base rate {rep['brier_base_rate']:.4f}")
    for fail in out.get("gate_failures") or []:
        console.print(f"  [yellow]gate:[/yellow] {fail}")


@app.command("swing-status")
def swing_status() -> None:
    """The swing registry: champion, shadow and recent models."""
    from skopaq.config import SkopaqConfig
    from skopaq.learning.registry import model_dir
    from skopaq.swing.model import swing_registry

    reg = swing_registry(model_dir(SkopaqConfig()))
    champ, shadow = reg.champion(), reg.shadow()
    console.print(f"champion: {champ['version'] if champ else None}")
    console.print(f"shadow:   {shadow['version'] if shadow else None}")
    for v in reg.versions()[-10:]:
        m = (v.get("report") or {}).get("model") or {}
        console.print(
            f"  {v['version']}  {v['state']:<9} net {m.get('net')} trades "
            f"{m.get('trades')} PF {m.get('profit_factor')}"
        )


@app.command("swing-promote")
def swing_promote(
    version: str = typer.Argument(..., help="The shadow model's version."),
    approved_by: str = typer.Option(..., "--by", help="Your name (recorded)."),
    reason: str = typer.Option(..., help="Why (recorded)."),
) -> None:
    """Shadow → champion after the shadow days (the only model live swing trades use)."""
    from skopaq.config import SkopaqConfig
    from skopaq.learning.registry import model_dir
    from skopaq.swing.model import SWING_GATES, swing_registry

    try:
        swing_registry(model_dir(SkopaqConfig())).promote(
            version, approved_by=approved_by, reason=reason, gates=SWING_GATES
        )
    except (KeyError, ValueError) as exc:
        console.print(f"[red]Not promoted:[/red] {exc}")
        raise typer.Exit(2)
    console.print(f"{version} is the swing champion (previous one kept for rollback)")


@app.command("swing-rollback")
def swing_rollback(
    by: str = typer.Option(..., help="Your name (recorded)."),
    reason: str = typer.Option(..., help="Why (recorded)."),
) -> None:
    """Make the previous swing champion the champion again."""
    from skopaq.config import SkopaqConfig
    from skopaq.learning.registry import model_dir
    from skopaq.swing.model import swing_registry

    prev = swing_registry(model_dir(SkopaqConfig())).rollback(by=by, reason=reason)
    console.print(f"Swing champion is now {prev}")


@app.command("charges-reconcile")
def charges_reconcile(
    file: str = typer.Argument(..., help="The broker's contract-note CSV export."),
    days: int = typer.Option(7, help="Days of the signal journal to compare against."),
    tolerance: float = typer.Option(1.0, help="₹ difference that counts as a discrepancy."),
) -> None:
    """Compare the broker's contract-note charges with Skopaq's estimates (no orders)."""
    from datetime import date, timedelta
    from pathlib import Path

    from skopaq.config import SkopaqConfig
    from skopaq.finance.reconcile import (
        estimates_from_signals,
        parse_contract_note_csv,
        reconcile,
        save_report,
    )
    from skopaq.strategy.journal import SignalJournal

    config = SkopaqConfig()
    try:
        notes = parse_contract_note_csv(Path(file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        console.print(f"Contract note not read: {exc}")
        raise typer.Exit(1)
    today = date.today()
    signals = SignalJournal.from_config(config).signals(
        sorted(today - timedelta(days=i) for i in range(days))
    )
    rep = reconcile(notes, estimates_from_signals(signals), tolerance=tolerance)
    d = rep.as_dict()
    console.print(
        f"{len(notes)} contract-note row(s): broker ₹{d['broker_total']:,.2f}, "
        f"estimated ₹{d['estimated_total']:,.2f}, difference ₹{d['difference']:+,.2f}"
    )
    for line in d["discrepancies"]:
        console.print(f"  [yellow]{line}[/yellow]")
    for key in d["unmatched_broker"]:
        console.print(f"  broker only: {key}")
    for key in d["unmatched_estimates"]:
        console.print(f"  estimate only: {key}")
    console.print(f"Saved {save_report(rep, by='cli')}")
    if d["discrepancies"]:
        raise typer.Exit(3)


@app.command("report")
def report(
    days: int = typer.Option(90, help="How many days back to include."),
) -> None:
    """Track record: the engines' signals, closed trades, confidence calibration.

    Read from the signal journal and the trades table (paper or live, per
    SKOPAQ_TRADING_MODE). Forward results only.
    """
    from skopaq.config import SkopaqConfig
    from skopaq.learning.report import build_report

    display_report(build_report(SkopaqConfig(), days=days))


@app.command("halt")
def halt(
    reason: str = typer.Argument("manual halt", help="Why trading is being halted."),
) -> None:
    """Kill switch: reject every BUY everywhere until `skopaq resume`.

    Applies to the daemon, `skopaq trade`, MCP and chat. SELLs stay allowed
    so open positions can still be protected.
    """
    from skopaq.execution import kill_switch

    try:
        where = kill_switch.halt(reason, by="cli")
    except RuntimeError as exc:  # neither the halt file nor Supabase could record it
        display_error(str(exc))
        raise typer.Exit(1)
    display_success(f"Trading HALTED: {reason}\nRecorded in: {', '.join(where)}")
    if "supabase:system_flags" not in where:
        display_error(
            "Not recorded in Supabase, so only this machine is halted. On other "
            "machines (e.g. the Railway daemon) set SKOPAQ_TRADING_HALTED=true."
        )


@app.command("resume")
def resume(
    yes: bool = typer.Option(False, "--yes", help="Resume without asking for confirmation."),
) -> None:
    """Lift the kill switch set by `skopaq halt`."""
    from skopaq.execution import kill_switch

    current = kill_switch.status(use_cache=False)
    if not current.halted:
        display_info("Trading is not halted.")
        return
    if not yes and not typer.confirm(f"{current.describe()}\nResume trading?"):
        display_info("Still halted.")
        return
    cleared = kill_switch.resume(by="cli")
    after = kill_switch.status(use_cache=False)
    if after.halted:
        display_error(f"Unset SKOPAQ_TRADING_HALTED to resume — {after.describe()}")
        raise typer.Exit(1)
    display_success(f"Trading resumed (cleared: {', '.join(cleared) or 'nothing'}).")


# ── Analyze ──────────────────────────────────────────────────────────────────


# ── Trade ────────────────────────────────────────────────────────────────────


async def _inject_paper_quote(config, paper, symbol: str) -> None:
    """Fetch a real quote from INDstocks and inject it into the paper engine.

    The paper engine requires a Quote in its ``_quotes`` cache before
    ``execute_order()`` can simulate a fill.  Without this, it returns
    "No quote available for {symbol}".
    """
    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.scrip_resolver import resolve_scrip_code
    from skopaq.broker.token_manager import TokenManager

    token_mgr = TokenManager()
    client = INDstocksClient(config, token_mgr)

    try:
        async with client:
            scrip_code = await resolve_scrip_code(client, symbol)
            logger.info("Resolved %s → %s", symbol, scrip_code)

            quote = await client.get_quote(scrip_code, symbol=symbol)
            paper.update_quote(quote)
            logger.info(
                "Injected quote: %s LTP=%.2f bid=%.2f ask=%.2f",
                symbol,
                quote.ltp,
                quote.bid,
                quote.ask,
            )
            return
    except Exception as exc:
        logger.warning("INDstocks quote for %s failed (%s): no paper fill price", symbol, exc)
    # All prices come from INDstocks: without a quote the paper engine refuses the fill
    logger.warning("No INDstocks quote for %s — paper fill may fail", symbol)


# ── Scan ──────────────────────────────────────────────────────────────────────


# ── Monitor ─────────────────────────────────────────────────────────────────


@app.command("monitor")
def monitor(
    poll_interval: int = typer.Option(0, help="Poll interval in seconds (0 = use config)."),
    stop_loss: float = typer.Option(0.0, help="Hard stop-loss % override (0 = use config)."),
    eod_exit_minutes: int = typer.Option(0, help="EOD exit minutes override (0 = use config)."),
) -> None:
    """Monitor open positions and auto-sell on their exit rules (stop, target, trail, EOD)."""
    from skopaq.config import SkopaqConfig

    config = SkopaqConfig()

    # Apply CLI overrides
    if poll_interval > 0:
        config.monitor_poll_interval_seconds = poll_interval
    if stop_loss > 0:
        config.monitor_hard_stop_pct = stop_loss
    if eod_exit_minutes > 0:
        config.monitor_eod_exit_minutes_before_close = eod_exit_minutes

    display_monitor_start(
        mode=config.trading_mode,
        poll_interval=config.monitor_poll_interval_seconds,
        stop_pct=config.monitor_hard_stop_pct,
        eod_minutes=config.monitor_eod_exit_minutes_before_close,
    )

    result = asyncio.run(_run_monitor(config))
    display_monitor_result(result)
    # Live: positions still open, a failed exit or an unconfirmed order at the end is
    # rc 4, which the scheduler turns into a "check the broker" alert
    if config.trading_mode == "live" and (result.positions_left or result.orders_unconfirmed):
        from skopaq.execution.daemon import POSITIONS_LEFT_EXIT_CODE

        raise typer.Exit(POSITIONS_LEFT_EXIT_CODE)


async def _run_monitor(config):
    """Async helper to run the position monitor."""
    import signal as sig

    from skopaq.broker.client import INDstocksClient
    from skopaq.broker.token_manager import TokenManager
    from skopaq.constants import PAPER_SAFETY_RULES, SAFETY_RULES
    from skopaq.execution.executor import Executor
    from skopaq.execution.order_router import OrderRouter
    from skopaq.execution.position_monitor import PositionMonitor
    from skopaq.execution.safety_checker import SafetyChecker

    # Always need an INDstocksClient for LTP polling
    token_mgr = TokenManager()
    client = INDstocksClient(config, token_mgr)

    # Wire live or paper backend
    from skopaq.broker.paper_engine import PaperEngine

    paper = PaperEngine(initial_capital=config.initial_paper_capital)
    live_client = None
    if config.trading_mode == "live":
        live_client = client  # reuse same client

    router = OrderRouter(config, paper, live_client=live_client)
    rules = PAPER_SAFETY_RULES if config.trading_mode == "paper" else SAFETY_RULES
    safety = SafetyChecker(
        rules=rules,
        max_sector_concentration_pct=config.max_sector_concentration_pct,
    )
    from skopaq.execution.pnl_history import seed_safety_checker

    seed_safety_checker(safety, config)
    from skopaq.execution.exit_plan import planner_from_config

    # The plans the daemon's BUYs made (stop-loss, target, what was booked)
    exit_planner = planner_from_config(config)
    executor = Executor(router, safety, exit_planner=exit_planner)

    # Graceful shutdown via Ctrl+C or SIGTERM (docker stop)
    stop_event = asyncio.Event()

    def _handle_sigint(*_):
        logger.info("Stop signal received — shutting down monitor gracefully...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for s in (sig.SIGINT, sig.SIGTERM):
        loop.add_signal_handler(s, _handle_sigint)
    watcher = _watch_dashboard_stop(config, stop_event)

    # Live ticks over the INDstocks price WebSocket (REST quotes when it is down)
    from skopaq.broker.websocket import feeds_from_config

    price_feed, _ = feeds_from_config(config, token_mgr)

    # Run monitor within client context
    async with client:
        if price_feed is not None:
            await price_feed.start()
        monitor_instance = PositionMonitor(
            executor=executor,
            client=client,
            router=router,
            config=config,
            stop_event=stop_event,
            # Record the exit and its P&L
            on_exit=lambda signal, execution: _record_exit(config, signal, execution),
            on_late_fill=lambda tracked, conf: _record_late_fill(config, tracked, conf),
            exit_planner=exit_planner,
            price_feed=price_feed,
        )
        try:
            return await monitor_instance.run()
        finally:
            watcher.cancel()
            if price_feed is not None:
                await price_feed.stop()


# ── Daemon ───────────────────────────────────────────────────────────────────


@app.command("daemon")
def daemon(
    max_trades: int = typer.Option(0, help="Override max trades per session (0 = use config)."),
    paper: bool = typer.Option(False, "--paper", help="Force paper mode."),
    live: bool = typer.Option(False, "--live", help="Force live mode (real orders on INDstocks)."),
    once: bool = typer.Option(
        False, "--once", help="Run immediately without waiting for market open."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Scanner only — print candidates, don't trade."
    ),
    confirm_live: bool = typer.Option(
        False, "--confirm-live", help="Skip interactive confirmation for live mode (for cron/CI)."
    ),
    ignore_calendar: bool = typer.Option(
        False,
        "--ignore-calendar",
        help="Run even on a weekend, an NSE holiday or after 15:30 IST (testing only).",
    ),
) -> None:
    """Run the autonomous trading daemon (scan -> trade -> monitor -> close)."""
    from skopaq.config import SkopaqConfig

    config = SkopaqConfig()

    # CLI overrides
    if paper:
        config.trading_mode = "paper"
    if live:
        config.trading_mode = "live"
    if max_trades > 0:
        config.daemon_max_trades_per_session = max_trades

    # NSE calendar gate: no session on weekends, NSE holidays or after the close.
    # Exits 0 so cron/scheduler runs on those days are not failures.
    if not dry_run and not ignore_calendar:
        from skopaq.risk import calendar as nse_calendar

        try:
            blocked = nse_calendar.daemon_block_reason(
                nse_calendar.now_ist(),
                config.nse_holidays,
            )
        except ValueError as exc:  # a malformed SKOPAQ_NSE_HOLIDAYS
            display_error(str(exc))
            raise typer.Exit(1)
        if blocked:
            display_info(f"No daemon session: {blocked}")
            raise typer.Exit(0)

    # Double-confirmation gate for LIVE mode — real money at stake
    if config.trading_mode == "live" and not dry_run:
        typer.echo(
            "\n  !!  LIVE DAEMON MODE — autonomous trading with real money.\n"
            f"     Max trades: {config.daemon_max_trades_per_session}\n"
            f"     Min profit: {config.daemon_min_profit_threshold_pct}% / "
            f"{config.daemon_min_profit_threshold_inr:.0f}\n"
        )
        if not confirm_live:
            if not typer.confirm("  Proceed with LIVE daemon?", default=False):
                typer.echo("  Aborted.")
                raise typer.Exit()

    display_daemon_start(config)
    report = asyncio.run(_run_daemon(config, once=once, dry_run=dry_run))
    display_daemon_report(report)
    if report.pre_open_failed:  # nothing traded: `skopaq schedule` may retry this morning
        from skopaq.execution.daemon import PRE_OPEN_FAILED_EXIT_CODE

        raise typer.Exit(PRE_OPEN_FAILED_EXIT_CODE)
    if report.failed:  # not for a candidate's failed analysis: the session itself ran
        raise typer.Exit(1)  # lets schedulers (skopaq schedule, Railway cron) see failed sessions


async def _run_daemon(config, *, once: bool = False, dry_run: bool = False):
    """Async helper to run the daemon session."""
    import signal as sig

    from skopaq.execution.daemon import TradingDaemon

    stop_event = asyncio.Event()

    # Graceful shutdown on SIGINT/SIGTERM
    def _handle_signal(signum, *_):
        sig_name = sig.Signals(signum).name
        logger.info("%s received — initiating graceful shutdown...", sig_name)
        stop_event.set()

    loop = asyncio.get_running_loop()
    for s in (sig.SIGINT, sig.SIGTERM):
        loop.add_signal_handler(s, _handle_signal, s)

    daemon_instance = TradingDaemon(config, stop_event=stop_event)
    # A stop asked for from the dashboard works like a SIGTERM
    watcher = _watch_dashboard_stop(config, stop_event)
    try:
        # Wait for market open (unless --once or --dry-run)
        if not once and not dry_run:
            await daemon_instance.wait_for_market_open()

        if stop_event.is_set():
            from skopaq.execution.daemon import DaemonSessionReport

            return DaemonSessionReport(session_date="cancelled")

        return await daemon_instance.run_session(dry_run=dry_run)
    finally:
        watcher.cancel()


def _watch_dashboard_stop(config, stop_event: asyncio.Event) -> asyncio.Task:
    """A task that sets ``stop_event`` on a dashboard stop request made from now on."""
    import time as _time

    from skopaq.execution.control import ControlChannel, watch_stop

    return asyncio.get_running_loop().create_task(
        watch_stop(ControlChannel.from_config(config), stop_event, started_at=_time.time())
    )


# ── Schedule ─────────────────────────────────────────────────────────────────


@app.command("schedule")
def schedule(
    check: bool = typer.Option(
        False,
        "--check",
        help="Print today's plan and the next session, then exit (1 if the configuration "
        "or this year's NSE holiday list is not usable).",
    ),
) -> None:
    """Run one daemon session per NSE trading day (always-on host; docs/deployment/mac-mini.md)."""
    from skopaq.config import SkopaqConfig
    from skopaq.execution.scheduler import (
        SchedulerState,
        ScheduleSettings,
        alert_invalid_config,
        check_ok,
        describe,
        reload_settings,
        run_forever,
    )
    from skopaq.risk import calendar as nse_calendar

    config = None
    try:
        config = SkopaqConfig()
        settings = ScheduleSettings.from_config(config)
    except ValueError as exc:
        display_error(str(exc))
        if not check and config is not None:  # a SkopaqConfig error stops every service anyway
            alert_invalid_config(config, str(exc))
        raise typer.Exit(1)

    if check:
        now = nse_calendar.now_ist()
        for line in describe(settings, now, SchedulerState(settings.state_dir, settings.log_dir)):
            typer.echo(line)
        raise typer.Exit(0 if check_ok(settings, now) else 1)

    # Settings saved from the dashboard are picked up between sessions.
    raise typer.Exit(run_forever(settings, reload=reload_settings))


# ── Chat ─────────────────────────────────────────────────────────────────────


# ── Serve ────────────────────────────────────────────────────────────────────


@app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address."),
    port: int = typer.Option(8000, help="Port."),
    reload: bool = typer.Option(False, help="Auto-reload on code changes."),
) -> None:
    """Start the FastAPI backend server."""
    import uvicorn

    display_serve_banner(host, port)
    uvicorn.run(
        "skopaq.api.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


# ── Version ──────────────────────────────────────────────────────────────────


@app.command("version")
def version() -> None:
    """Print version."""
    display_version(__version__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _compute_risk_scales(config, trade_date: str) -> tuple[float, float]:
    """Compute regime and calendar position-sizing multipliers.

    Returns:
        (regime_scale, calendar_scale) — both default to 1.0 when disabled.
    """
    from datetime import date as date_cls

    regime_scale = 1.0
    calendar_scale = 1.0

    # Regime detection (India VIX + NIFTY trend)
    if config.regime_detection_enabled:
        try:
            from skopaq.risk.regime import RegimeDetector, fetch_regime_data

            india_vix, nifty_price, nifty_sma200 = fetch_regime_data()
            detector = RegimeDetector()
            regime = detector.detect(india_vix, nifty_price, nifty_sma200)
            regime_scale = regime.position_scale

            if not regime.should_trade:
                logger.warning(
                    "Regime detector says NO TRADE: %s VIX=%.1f",
                    regime.label,
                    regime.vix or 0,
                )
        except Exception:
            logger.warning("Regime detection failed — using default scale", exc_info=True)

    # NSE Event Calendar
    try:
        from skopaq.risk.calendar import NSEEventCalendar

        cal = NSEEventCalendar()
        try:
            d = date_cls.fromisoformat(trade_date)
        except (ValueError, TypeError):
            d = date_cls.today()

        calendar_scale = cal.get_position_scale(d)
        events = cal.get_events(d)
        if events:
            logger.info("Calendar events for %s: %s (scale=%.1f)", d, events, calendar_scale)
    except Exception:
        logger.warning("Event calendar check failed — using default scale", exc_info=True)

    return regime_scale, calendar_scale


async def _record_exit(config, signal, execution, *, rollback_unbooked: bool = False) -> bool:
    """Persist an exit (monitor, EOD close, dashboard close) as a trade row.

    It goes through the same booking as an entry, so the opening
    BUY row is closed with its realized P&L, which the loss limits read back
    (``skopaq.execution.pnl_history``). False when it was not booked
    (``_run_lifecycle``, which ``rollback_unbooked`` is passed to).
    """
    from datetime import timedelta

    from skopaq.execution.trade_booking import TradeResult

    ist_today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()
    result = TradeResult(
        symbol=signal.symbol,
        trade_date=ist_today,
        signal=signal,
        execution=execution,
    )
    return await _run_lifecycle(config, result, rollback_unbooked=rollback_unbooked)


def _trade_repository(config):
    """A TradeRepository over Supabase, or None when it is not configured or unreachable."""
    if not config.supabase_url or not config.supabase_service_key.get_secret_value():
        return None
    try:
        from skopaq.db.repositories import TradeRepository
        from supabase import create_client

        return TradeRepository(
            create_client(config.supabase_url, config.supabase_service_key.get_secret_value())
        )
    except Exception:
        logger.warning("Supabase unavailable — trade rows not written", exc_info=True)
        return None


def _delta_price(avg_now, filled_now, avg_before, filled_before):
    """Average price of the shares filled since the last report: (P·F − p·f) / (F − f)
    when both averages are known, else the current average (None if unknown)."""
    from decimal import Decimal

    if avg_now is None:
        return None
    if avg_before is None or not filled_before:
        return avg_now
    price = (avg_now * filled_now - avg_before * filled_before) / (filled_now - filled_before)
    return price.quantize(Decimal("0.0001")) if price > 0 else avg_now


async def _record_late_fill(config, tracked, conf) -> bool:
    """Persist a live fill the broker confirmed after its order had been reported.

    The monitor or CLOSING resumed the order (``tracked``) and read its final state
    (``conf``); only the shares beyond ``tracked.filled_reported`` are new.

    - BUY: the order's open row gets the new quantity and average price; a row already
      closed gets a separate row for the extra shares (``order_id`` None: it is UNIQUE);
      a BUY that was reported failed or unconfirmed gets its row now.
    - SELL (a stuck exit that filled later): the extra shares close BUY rows like any
      other exit, at their own average price.

    Returns False when the trade rows could not be written, so the caller does not count
    the shares as booked; True otherwise — also when Supabase is not configured (there
    are no rows to write) and when an exit with no price at all is left to the user
    (CRITICAL ``exit-late-unpriced``), so that no process books it again.
    """
    from decimal import Decimal

    from skopaq.broker.models import ExecutionResult, OrderType, TradingSignal
    from skopaq.db.models import TradeRecord

    filled = conf.filled_qty
    if filled is None or filled <= tracked.filled_reported:
        return True
    delta = filled - tracked.filled_reported
    price = _delta_price(
        conf.avg_price, filled, tracked.avg_price_reported, tracked.filled_reported
    )
    # INDstocks' flat fee is per order that fills: charged now unless already reported
    fee = Decimal("0") if tracked.filled_reported else Decimal("20")

    if tracked.side == "SELL":
        from skopaq.execution.order_alerts import get_alerter

        source = conf.price_source
        if not price:
            # No broker price: the exit's reference price (the LTP when it was sent) or
            # its limit price, as a normal exit would use — never a P&L of 0
            estimate = getattr(tracked.signal, "entry_price", None) or tracked.price
            if not estimate:
                get_alerter().alert(
                    "CRITICAL",
                    f"exit-late-unpriced:{tracked.order_id}",
                    f"SELL {tracked.symbol}: order {tracked.order_id} filled {delta} more but "
                    "the broker gave no price and there is no estimate; the trade rows are "
                    "left open — close them by hand at the broker's fill price",
                    order_ids=[tracked.order_id],
                )
                # Left to the user: counted as handled, so no process books them after
                # the user has
                return True
            price, source = Decimal(str(estimate)), "estimate"
            get_alerter().alert(
                "WARNING",
                f"fill-price-unknown:{tracked.order_id}",
                f"SELL {tracked.symbol}: order {tracked.order_id} filled {delta} more but the "
                f"broker gave no price; recorded at the estimate {price}",
                order_ids=[tracked.order_id],
            )
        signal = TradingSignal(
            symbol=tracked.symbol,
            action="SELL",
            confidence=100,
            entry_price=float(price),
            order_type=OrderType.MARKET,
            quantity=delta,
            reasoning=f"Late fill of exit order {tracked.order_id}",
        )
        execution = ExecutionResult(
            success=True,
            signal=signal,
            mode="live",
            fill_price=float(price),
            brokerage=float(fee),
            filled_quantity=delta,
            requested_quantity=delta,
            outcome="late_fill",
            order_ids=[tracked.order_id],
            fill_price_source=source,
            broker_message=f"late fill of {tracked.order_id}",
        )
        # Not booked: its SELL row is removed again, as the next booking writes its own
        booked = await _record_exit(config, signal, execution, rollback_unbooked=True)
        return booked is not False

    repo = _trade_repository(config)
    if repo is None:
        # Not configured: nothing to book; configured but unreachable: not booked
        return not (config.supabase_url and config.supabase_service_key.get_secret_value())
    from skopaq.execution.order_alerts import get_alerter

    avg, source = conf.avg_price, conf.price_source
    if not avg:
        # No broker price: the order's limit price (or its signal's reference price) as the
        # cost basis, as a normal entry would use — a row without one books its exit at 0
        estimate = tracked.price or getattr(tracked.signal, "entry_price", None)
        if estimate:
            avg, source = Decimal(str(estimate)), "estimate"
            price = price or avg
            get_alerter().alert(
                "WARNING",
                f"fill-price-unknown:{tracked.order_id}",
                f"BUY {tracked.symbol}: order {tracked.order_id} filled {delta} more but the "
                f"broker gave no price; recorded at the estimate {avg}",
                order_ids=[tracked.order_id],
            )
        else:
            get_alerter().alert(
                "CRITICAL",
                f"late-fill-unpriced:{tracked.order_id}",
                f"BUY {tracked.symbol}: order {tracked.order_id} filled {delta} more but the "
                "broker gave no price and there is no estimate; its trade row has no cost "
                "basis — set it by hand from the broker's fill price, or its exit books a "
                "P&L of 0",
                order_ids=[tracked.order_id],
            )
    broker = {
        "outcome": "late_fill",
        "order_ids": [tracked.order_id],
        "late_fill_of": tracked.order_id,
        "fill_price_source": source,
    }
    try:
        row = repo.find_by_order_id(tracked.order_id)
        if row is not None and row.closed_at is None:
            updates = {"quantity": str(filled)}
            if conf.avg_price or (avg and not (row.fill_price or row.price)):
                updates["fill_price"] = str(avg)
            repo.update(row.id, updates)
        elif row is not None:
            repo.insert(
                TradeRecord(
                    symbol=tracked.symbol,
                    side="BUY",
                    quantity=delta,
                    order_id=None,
                    fill_price=price,
                    price=price,
                    status="COMPLETE",
                    is_paper=False,
                    brokerage=Decimal("0"),
                    signal_source="skopaq-ai",
                    entry_reason=f"Late fill of {tracked.order_id} after its row was closed",
                    model_signals={"broker": broker},
                )
            )
        else:
            repo.insert(
                TradeRecord(
                    symbol=tracked.symbol,
                    side="BUY",
                    quantity=filled,
                    order_id=tracked.order_id,
                    fill_price=avg,
                    price=avg,
                    status="COMPLETE",
                    is_paper=False,
                    brokerage=fee,
                    signal_source="skopaq-ai",
                    entry_reason=f"Late fill of {tracked.order_id} adopted by the monitor",
                    model_signals={"broker": broker},
                )
            )
        logger.warning(
            "Late fill of BUY %s (%s %s) recorded", tracked.order_id, delta, tracked.symbol
        )
        return True
    except Exception:
        logger.warning("Recording the late fill of %s failed", tracked.order_id, exc_info=True)
        return False


async def _run_lifecycle(config, result, *, rollback_unbooked: bool = False) -> bool:
    """Book a trade (``skopaq.execution.trade_booking.TradeResult``).

    Flow:
        1. Persist the trade to Supabase (so find_open_buy() works for future SELLs)
        2. Book the BUY → SELL linkage and the realized P&L

    Silently does nothing if Supabase is not configured (True: nothing to write).
    Returns False when the trade was not booked: a BUY's row could not be inserted, the
    lifecycle failed, or a live SELL closed no BUY row because the rows could not be read
    (a caller that books it again — a late fill — relies on it; the others log it). A
    SELL whose row could not be inserted but whose BUY rows were closed is booked: its
    P&L is. ``rollback_unbooked``: a SELL that closed no row has its row removed again,
    so booking it again does not leave a second one.
    """
    if not config.supabase_url or not config.supabase_service_key.get_secret_value():
        return True

    booked = True
    try:
        from skopaq.db.repositories import TradeRepository
        from skopaq.execution.trade_booking import TradeLifecycleManager
        from supabase import create_client

        client = create_client(
            config.supabase_url,
            config.supabase_service_key.get_secret_value(),
        )
        trade_repo = TradeRepository(client)

        # Step 1: Persist the trade BEFORE lifecycle processing.
        # This is the critical link — without it, find_open_buy() never finds
        # BUYs and their P&L is never booked.
        trade_record = _build_trade_record(result, config)
        saved = None
        if trade_record is not None:
            try:
                saved = trade_repo.insert(trade_record)
                result.trade_id = saved.id
                logger.info(
                    "Trade persisted: %s %s id=%s (paper=%s)",
                    trade_record.side,
                    trade_record.symbol,
                    saved.id,
                    trade_record.is_paper,
                )
            except Exception:
                if trade_record.side != "SELL":
                    booked = False  # a BUY is booked by its row alone
                logger.warning(
                    "Failed to persist trade to Supabase — lifecycle will "
                    "continue but BUY/SELL linkage may not work",
                    exc_info=True,
                )

        # Step 2: BUY/SELL linkage and the realized P&L
        lifecycle = TradeLifecycleManager(trade_repo)
        if await lifecycle.on_trade(result) is False:
            booked = False  # a SELL that closed no BUY row
            if rollback_unbooked and saved is not None:
                try:
                    trade_repo.delete(saved.id)
                    result.trade_id = None
                except Exception:
                    logger.warning(
                        "Removing the unbooked SELL row %s failed", saved.id, exc_info=True
                    )
    except Exception:
        logger.warning("Trade lifecycle processing failed", exc_info=True)
        return False
    return booked


# The trades table's product vocabulary (CHECK: CNC, MIS, NRML)
_ROW_PRODUCT = {"INTRADAY": "MIS", "MIS": "MIS", "MARGIN": "NRML", "NRML": "NRML", "CNC": "CNC"}


def _product_of(signal) -> str:
    """The trade row's product: the signal's (the scalper's and the options engine's
    INTRADAY is written MIS, MARGIN NRML), else CNC."""
    product = getattr(signal, "product", None)
    value = getattr(product, "value", product)
    if not isinstance(value, str) or not value:
        return "CNC"
    return _ROW_PRODUCT.get(value.upper(), "CNC")


def _fno_signals(signal) -> dict:
    """An F&O trade's contract (segment, security id, lot size, lots) for model_signals;
    {} for equity."""
    if getattr(signal, "is_derivative", False) is not True:
        return {}
    lot = int(getattr(signal, "lot_size", 1) or 1)
    qty = int(getattr(signal, "quantity", 0) or 0)
    return {
        "segment": "DERIVATIVE",
        "security_id": str(signal.security_id or ""),
        "lot_size": lot,
        "lots": qty // lot if lot else qty,
    }


def _agent_decision(signal) -> dict:
    """The decision stored on a trade row; a BUY's exit plan (stop-loss, target) too,
    which the dashboard shows on open positions."""
    decision = {"action": signal.action, "confidence": signal.confidence}
    for key in ("stop_loss", "target"):
        value = getattr(signal, key, None)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            decision[key] = round(float(value), 2)
    return decision


def _build_trade_record(result, config):
    """Convert a TradeResult into a TradeRecord for Supabase persistence.

    Returns None if the result doesn't represent a successful execution
    (e.g., HOLD signals, failed orders, or analysis-only runs).
    """
    if result.signal is None:
        return None
    if result.execution is None or not result.execution.success:
        return None
    if result.signal.action == "HOLD":
        return None

    from decimal import Decimal

    from skopaq.broker.models import (
        filled_quantity_of,
        is_remaining_open,
        order_ids_of,
        outcome_of,
    )
    from skopaq.db.models import TradeRecord

    execution = result.execution

    # model_signals: the engine's evidence (swing setup, model score, signal id)
    model_signals = {}
    evidence = getattr(result, "evidence", None)
    if isinstance(evidence, dict) and evidence:
        model_signals["swing"] = {k: v for k, v in evidence.items() if v is not None}
    if getattr(result, "duration_seconds", 0):
        model_signals["duration_seconds"] = result.duration_seconds

    # Live: the row is what the broker confirmed — the filled quantity, the order ids
    # (none on a late-fill row: trades.order_id is UNIQUE and the order may be on a row)
    order_id = None
    if execution.mode == "live":
        ids = order_ids_of(execution)
        late = outcome_of(execution) == "late_fill"
        if not late:
            order_id = ",".join(ids)[:255] or None
        requested = execution.requested_quantity
        broker = {
            "outcome": outcome_of(execution),
            "requested_qty": _json_number(requested) if requested is not None else None,
            "fill_price_source": execution.fill_price_source,
            "remaining_open": is_remaining_open(execution),
            "fill_unconfirmed": execution.fill_unconfirmed is True,
            "order_ids": ids,
        }
        if late and ids:
            broker["late_fill_of"] = ids[0]
        model_signals["broker"] = broker
    fno_info = _fno_signals(result.signal)
    if fno_info:
        model_signals["fno"] = fno_info

    # Determine exchange and product based on asset class
    is_crypto = config.asset_class == "crypto"
    trade_symbol = result.symbol
    if is_crypto:
        # result.symbol may be the yfinance format (BTC-USD); restore Binance pair
        from skopaq.broker.crypto_symbols import to_binance_pair

        trade_symbol = to_binance_pair(result.symbol, config.crypto_quote_currency)

    return TradeRecord(
        symbol=trade_symbol,
        exchange="BINANCE" if is_crypto else _row_exchange(result.signal),
        product="SPOT" if is_crypto else _product_of(result.signal),
        side=result.signal.action,
        quantity=filled_quantity_of(execution, result.signal.quantity or Decimal("1")),
        order_id=order_id,
        price=(Decimal(str(result.signal.entry_price)) if result.signal.entry_price else None),
        fill_price=(
            Decimal(str(result.execution.fill_price)) if result.execution.fill_price else None
        ),
        slippage=(
            Decimal(str(result.execution.slippage)) if result.execution.slippage else Decimal("0")
        ),
        brokerage=Decimal(str(result.execution.brokerage)),
        is_paper=(result.execution.mode == "paper"),
        status="COMPLETE",
        signal_source="skopaq-ai",
        consensus_score=result.signal.confidence,
        entry_reason=(result.signal.reasoning[:2000] if result.signal.reasoning else None),
        agent_decision=_agent_decision(result.signal),
        model_signals=model_signals,
    )


def _row_exchange(signal) -> str:
    """NSE or BSE (the trades table allows only those; an F&O row says so in
    model_signals["fno"])."""
    exchange = getattr(getattr(signal, "exchange", None), "value", "NSE")
    return exchange if exchange in ("NSE", "BSE") else "NSE"


def _json_number(value):
    """A Decimal as a JSON number (int when whole) for model_signals."""
    number = float(value)
    return int(number) if number == int(number) else number


def _setup_logging(level: str = "INFO") -> None:
    """Configure root logger."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S %Z",
    )


if __name__ == "__main__":
    _setup_logging()
    app()
