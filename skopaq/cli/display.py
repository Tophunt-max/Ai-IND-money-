"""Rich display functions for every SkopaqTrader CLI command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from rich import box
from rich.align import Align
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from skopaq.cli.theme import (
    ACCENT,
    BRAND,
    BRAND_DIM,
    DIM,
    ERROR,
    ERROR_BORDER,
    FAIL,
    HEADER_BORDER,
    INFO_BORDER,
    MUTED,
    OK,
    STATUS_BORDER,
    SUCCESS,
    WARN,
    WARNING,
    WARNING_BORDER,
    console,
)

if TYPE_CHECKING:
    from skopaq.broker.token_manager import TokenHealth
    from skopaq.config import SkopaqConfig
    from skopaq.execution.daemon import DaemonSessionReport
    from skopaq.execution.position_monitor import MonitorResult
    from skopaq.learning.report import Report

_STATIC_DIR = Path(__file__).parent / "static"


# ── Welcome Banner ────────────────────────────────────────────────────────────


def display_welcome() -> None:
    """Show branded ASCII art welcome banner with workflow summary."""
    banner_path = _STATIC_DIR / "welcome.txt"
    if banner_path.exists():
        ascii_art = banner_path.read_text()
    else:
        ascii_art = "  SkopaqTrader"

    content = Text()
    content.append(ascii_art, style=BRAND_DIM)
    content.append("\n")
    content.append(
        "Adaptive-AI algorithmic trading for Indian equities\n",
        style="bold white",
    )
    content.append("Integrates with INDstocks", style=DIM)
    content.append("\n\n")
    content.append("Workflow:  ", style="bold")
    content.append("Setups", style="cyan")
    content.append(" \u2192 ", style=DIM)
    content.append("Adaptive AI", style="green")
    content.append(" \u2192 ", style=DIM)
    content.append("Signal gate", style="yellow")
    content.append(" \u2192 ", style=DIM)
    content.append("Risk", style="magenta")
    content.append(" \u2192 ", style=DIM)
    content.append("Execution", style="bold green")

    panel = Panel(
        Align.center(content),
        border_style=HEADER_BORDER,
        padding=(1, 2),
        title="[bold cyan]SkopaqTrader[/bold cyan]",
        subtitle="[dim]Adaptive AI · paper by default[/dim]",
    )
    console.print(panel)
    console.print()


# ── Status Command ────────────────────────────────────────────────────────────


def display_status(
    version: str,
    config: SkopaqConfig,
    health: TokenHealth,
    halt: Optional[Any] = None,
) -> None:
    """Render the full ``skopaq status`` dashboard.

    ``halt`` is a ``kill_switch.HaltStatus``; a halt is shown under Mode.
    """
    table = Table(
        show_header=False,
        box=box.SIMPLE,
        padding=(0, 2),
        expand=True,
    )
    table.add_column("Key", style="bold", width=14)
    table.add_column("Value")

    # Version
    table.add_row("Version", f"[{BRAND}]v{version}[/{BRAND}]")

    # Mode
    mode = config.trading_mode.upper()
    mode_style = SUCCESS if mode == "PAPER" else "bold red"
    table.add_row("Mode", f"[{mode_style}]{mode}[/{mode_style}]")
    if halt is not None and halt.halted:
        table.add_row("Trading", f"[bold red]{FAIL}  {halt.describe()}[/bold red]")

    # Broker
    table.add_row("Broker", f"INDstocks ({config.indstocks_base_url})")

    # Token
    if health.valid:
        table.add_row(
            "Token",
            f"{OK}  VALID ({health.remaining} remaining)",
        )
        if health.warning:
            table.add_row("", f"{WARN}  {health.warning}")
    else:
        table.add_row(
            "Token",
            f"{FAIL}  INVALID \u2014 {health.warning}",
        )

    # Supabase
    if config.supabase_url:
        table.add_row("Supabase", f"{OK}  configured")
    else:
        table.add_row("Supabase", f"{WARN}  NOT configured")

    # Redis
    if config.upstash_redis_url:
        table.add_row("Redis", f"{OK}  Upstash")
    else:
        table.add_row("Redis", f"{WARN}  NOT configured")

    panel = Panel(
        table,
        title="[bold]System Health[/bold]",
        border_style=STATUS_BORDER,
        padding=(1, 1),
    )
    console.print(panel)


# ── Analyze Command ───────────────────────────────────────────────────────────


# ── Trade Command ─────────────────────────────────────────────────────────────


# ── Scan Command ──────────────────────────────────────────────────────────────


def _fmt_pct(value: Optional[float], signed: bool = True) -> str:
    if value is None:
        return "—"
    return f"{value:+.1%}" if signed else f"{value:.0%}"


def _fmt_inr(value: Optional[float]) -> str:
    return "—" if value is None else f"₹{value:,.0f}"


def display_report(report: Report) -> None:
    """``skopaq report``: the engines' signals, executed trades, confidence calibration."""
    from skopaq.learning.report import MIN_SAMPLE

    sig, trades = report.signals, report.trades

    summary = Table(show_header=False, box=box.SIMPLE, padding=(0, 2))
    summary.add_column("Key", style="bold")
    summary.add_column("Value")
    summary.add_row("Signals", f"{sig.total} ({sig.qualified} qualified, {sig.rejected} rejected)")
    summary.add_row("Executed / closed", f"{sig.executed} / {sig.closed} "
                    f"({_fmt_pct(sig.win_rate, signed=False)} won, net {_fmt_inr(sig.net)})")
    summary.add_row("Closed trades",
                    f"{trades.closed} ({_fmt_pct(trades.win_rate, signed=False)} won)")
    summary.add_row("Realized P&L", _fmt_inr(trades.total_pnl))
    summary.add_row("Avg win / loss", f"{_fmt_inr(trades.avg_win)} / {_fmt_inr(trades.avg_loss)}")
    summary.add_row(
        "Profit factor", "—" if trades.profit_factor is None else f"{trades.profit_factor:.2f}"
    )
    summary.add_row("Max drawdown", _fmt_inr(trades.max_drawdown))
    summary.add_row(
        "Per-trade return",
        f"{_fmt_pct(trades.avg_return)} avg, {_fmt_pct(trades.return_std, signed=False)} std",
    )

    strategies = Table(box=box.ROUNDED, header_style="bold magenta", padding=(0, 1))
    for column in ("Strategy", "Decisions", "Qualified", "Executed", "Closed", "Won", "Net"):
        strategies.add_column(column, justify="left" if column == "Strategy" else "right")
    for name, st in sorted(sig.by_strategy.items()):
        strategies.add_row(
            name,
            str(st.decisions),
            str(st.qualified),
            str(st.executed),
            str(st.closed),
            _fmt_pct(st.win_rate, signed=False),
            _fmt_inr(st.net),
        )

    calib = Table(box=box.ROUNDED, header_style="bold magenta", padding=(0, 1))
    for column in ("Confidence", "Trades", "Won"):
        calib.add_column(column, justify="left" if column == "Confidence" else "right")
    for bucket in report.calibration:
        calib.add_row(bucket.label, str(bucket.trades), _fmt_pct(bucket.win_rate, signed=False))

    title = f"[bold]Track record[/bold]  [{DIM}]last {report.days} days · {report.mode}[/{DIM}]"
    console.print(Panel(summary, title=title, border_style=HEADER_BORDER, padding=(1, 1)))
    console.print(Panel(strategies, title="[bold]By strategy[/bold]", border_style=HEADER_BORDER))
    console.print(
        Panel(calib, title="[bold]Confidence calibration[/bold]", border_style=HEADER_BORDER)
    )
    if not sig.enough_data:
        console.print(
            f"[{WARNING}]{WARN}  Only {sig.closed} closed signals; below "
            f"{MIN_SAMPLE}, win rates are mostly noise.[/{WARNING}]"
        )
    console.print(f"[{DIM}]Sources: {', '.join(report.sources) or 'none'}[/{DIM}]")


# ── Token Commands ────────────────────────────────────────────────────────────


def display_token_health(health: TokenHealth) -> None:
    """Render token health as a styled panel."""
    table = Table(show_header=False, box=box.SIMPLE, padding=(0, 2), expand=True)
    table.add_column("Key", style="bold", width=12)
    table.add_column("Value")

    if health.valid:
        table.add_row("Status", f"{OK}  [bold green]VALID[/bold green]")
        if health.expires_at:
            table.add_row("Expires", str(health.expires_at))
        if health.remaining:
            table.add_row("Remaining", str(health.remaining))
        if health.warning:
            table.add_row("Warning", f"{WARN}  [{WARNING}]{health.warning}[/{WARNING}]")
        border = STATUS_BORDER
    else:
        table.add_row("Status", f"{FAIL}  [bold red]INVALID[/bold red]")
        if health.warning:
            table.add_row("Reason", f"[red]{health.warning}[/red]")
        border = ERROR_BORDER

    panel = Panel(
        table,
        title="[bold]Token Health[/bold]",
        border_style=border,
        padding=(1, 1),
    )
    console.print(panel)


def display_token_set(health: TokenHealth) -> None:
    """Confirm token was stored successfully."""
    content = Text()
    content.append(f"{OK}  ", style="")
    content.append("Token stored successfully\n\n", style=SUCCESS)
    if health.expires_at:
        content.append("Expires:   ", style="bold")
        content.append(f"{health.expires_at}\n")
    if health.remaining:
        content.append("Remaining: ", style="bold")
        content.append(f"{health.remaining}")

    console.print(Panel(content, border_style=STATUS_BORDER, padding=(1, 2)))


# ── Serve Command ─────────────────────────────────────────────────────────────


def display_serve_banner(host: str, port: int) -> None:
    """Show server startup banner."""
    content = Text()
    content.append("SkopaqTrader API\n", style=BRAND)
    content.append("Listening on ", style=DIM)
    content.append(f"http://{host}:{port}", style="bold underline")
    content.append("\n")
    content.append("Health:    ", style="bold")
    content.append(f"http://{host}:{port}/health", style=DIM)

    console.print(
        Panel(
            Align.center(content),
            border_style=STATUS_BORDER,
            padding=(1, 2),
            title="[bold]Server[/bold]",
        )
    )


# ── Version Command ───────────────────────────────────────────────────────────


def display_version(version: str) -> None:
    """Show styled version text."""
    console.print(f"[{BRAND}]SkopaqTrader[/{BRAND}] [{DIM}]v{version}[/{DIM}]")


# ── Generic Messages ──────────────────────────────────────────────────────────


def display_error(message: str) -> None:
    """Show error message in a red-bordered panel."""
    console.print(
        Panel(
            f"{FAIL}  [{ERROR}]{message}[/{ERROR}]",
            border_style=ERROR_BORDER,
            title="[bold red]Error[/bold red]",
            padding=(0, 2),
        )
    )


def display_info(message: str) -> None:
    """Show informational message in a blue-bordered panel."""
    console.print(
        Panel(
            f"[{BRAND_DIM}]{message}[/{BRAND_DIM}]",
            border_style=INFO_BORDER,
            padding=(0, 2),
        )
    )


def display_success(message: str) -> None:
    """Show success message in a green-bordered panel."""
    console.print(
        Panel(
            f"{OK}  [{SUCCESS}]{message}[/{SUCCESS}]",
            border_style=STATUS_BORDER,
            padding=(0, 2),
        )
    )


# ── Monitor Command ──────────────────────────────────────────────────────────


def display_monitor_start(
    mode: str,
    poll_interval: int,
    stop_pct: float,
    eod_minutes: int,
) -> None:
    """Show monitor startup configuration panel."""
    table = Table(
        show_header=False,
        box=box.SIMPLE,
        padding=(0, 2),
        expand=True,
    )
    table.add_column("Key", style="bold", width=16)
    table.add_column("Value")

    mode_style = SUCCESS if mode == "paper" else "bold red"
    table.add_row("Mode", f"[{mode_style}]{mode.upper()}[/{mode_style}]")
    table.add_row("Poll Interval", f"{poll_interval}s")
    table.add_row("Hard Stop", f"{stop_pct:.0%}")
    table.add_row("EOD Exit", f"{eod_minutes} min before close")

    panel = Panel(
        table,
        title="[bold cyan]Position Monitor[/bold cyan]",
        border_style=HEADER_BORDER,
        padding=(1, 1),
    )
    console.print(panel)
    console.print()


def display_monitor_tick(
    symbol: str,
    ltp: float,
    pnl_pct: float,
    action: str = "",
) -> None:
    """Show single-line status for a poll cycle."""
    pnl_style = SUCCESS if pnl_pct >= 0 else ERROR
    line = (
        f"  [{ACCENT}]{symbol}[/{ACCENT}]"
        f"  LTP=[bold]₹{ltp:,.2f}[/bold]"
        f"  P&L=[{pnl_style}]{pnl_pct:+.2f}%[/{pnl_style}]"
    )
    if action:
        line += f"  → [{WARNING}]{action}[/{WARNING}]"
    console.print(line)


def display_monitor_result(result: MonitorResult) -> None:
    """Show session summary when the monitor exits."""
    table = Table(
        show_header=False,
        box=box.ROUNDED,
        padding=(0, 2),
        expand=True,
    )
    table.add_column("Field", style="bold", width=16)
    table.add_column("Value")

    table.add_row("Positions", str(result.positions_monitored))
    table.add_row("Cycles", str(result.cycles))
    table.add_row("Sells Executed", f"[bold green]{result.sells_executed}[/bold green]")

    if result.sells_failed:
        table.add_row("Sells Failed", f"[bold red]{result.sells_failed}[/bold red]")

    pnl_style = SUCCESS if result.total_pnl >= 0 else ERROR
    table.add_row(
        "Total P&L",
        f"[{pnl_style}]₹{result.total_pnl:,.2f}[/{pnl_style}]",
    )

    for reason in result.exit_reasons:
        table.add_row("Exit", f"[{DIM}]{reason}[/{DIM}]")

    # Live: what the monitor leaves open (`skopaq monitor` then exits 4)
    if result.late_fills:
        table.add_row("Late Fills", str(result.late_fills))
    if result.exits_blocked:
        table.add_row("Exits Blocked", f"[{ERROR}]{', '.join(result.exits_blocked)}[/{ERROR}]")
    if result.positions_left:
        table.add_row("Still Open", f"[{ERROR}]{', '.join(result.positions_left)}[/{ERROR}]")
    if result.orders_unconfirmed:
        table.add_row(
            "Unconfirmed",
            f"[{ERROR}]{', '.join(result.orders_unconfirmed)} — check the broker[/{ERROR}]",
        )

    left_open = result.positions_left or result.orders_unconfirmed
    border = STATUS_BORDER if result.sells_failed == 0 and not left_open else WARNING_BORDER
    panel = Panel(
        table,
        title="[bold]Monitor Summary[/bold]",
        border_style=border,
        padding=(1, 1),
    )
    console.print(panel)


# ── Daemon Command ───────────────────────────────────────────────────────────


def display_daemon_start(config: SkopaqConfig) -> None:
    """Show daemon startup configuration panel."""
    table = Table(
        show_header=False,
        box=box.SIMPLE,
        padding=(0, 2),
        expand=True,
    )
    table.add_column("Key", style="bold", width=18)
    table.add_column("Value")

    mode = config.trading_mode.upper()
    mode_style = SUCCESS if mode == "PAPER" else "bold red"
    table.add_row("Mode", f"[{mode_style}]{mode}[/{mode_style}]")
    table.add_row("Max Trades", str(config.daemon_max_trades_per_session))
    table.add_row("Max Swing Ideas", str(config.daemon_max_candidates_to_analyze))
    table.add_row("Scan Delay", f"{config.daemon_scan_delay_after_open_seconds}s after open")
    table.add_row("Hard Stop", f"{config.monitor_hard_stop_pct:.0%}")
    table.add_row("EOD Exit", f"{config.monitor_eod_exit_minutes_before_close} min before close")
    table.add_row("Swing AI", "setups + swing model (skopaq swing-status)")

    panel = Panel(
        table,
        title="[bold cyan]Autonomous Daemon[/bold cyan]",
        border_style=HEADER_BORDER,
        padding=(1, 1),
    )
    console.print(panel)
    console.print()


def display_daemon_report(report: DaemonSessionReport) -> None:
    """Show end-of-session daemon report."""
    table = Table(
        show_header=False,
        box=box.ROUNDED,
        padding=(0, 2),
        expand=True,
    )
    table.add_column("Field", style="bold", width=18)
    table.add_column("Value")

    table.add_row("Session Date", report.session_date)
    table.add_row("Candidates Scanned", str(report.candidates_scanned))
    table.add_row("Swing Ideas Tried", str(report.candidates_analyzed))
    table.add_row(
        "Trades Opened",
        f"[bold green]{report.trades_opened}[/bold green]",
    )

    if report.trades_rejected:
        table.add_row(
            "Trades Rejected",
            f"[bold red]{report.trades_rejected}[/bold red]",
        )

    table.add_row("Holds", str(report.holds))
    table.add_row(
        "Sells Executed",
        f"[bold green]{report.sells_executed}[/bold green]",
    )

    if report.sells_failed:
        table.add_row(
            "Sells Failed",
            f"[bold red]{report.sells_failed}[/bold red]",
        )

    pnl_style = SUCCESS if report.gross_pnl >= 0 else ERROR
    table.add_row(
        "Gross P&L",
        f"[{pnl_style}]{report.gross_pnl:,.2f}[/{pnl_style}]",
    )

    # Live: what the session could not confirm or close
    if report.orders_unconfirmed:
        table.add_row("Unconfirmed Orders", f"[{ERROR}]{report.orders_unconfirmed}[/{ERROR}]")
    if report.exits_blocked:
        table.add_row("Exits Blocked", f"[{ERROR}]{', '.join(report.exits_blocked)}[/{ERROR}]")
    if report.positions_left:
        table.add_row(
            "Positions Left",
            f"[{ERROR}]{', '.join(report.positions_left)} — check the broker[/{ERROR}]",
        )

    # Phase timings
    if report.phase_times:
        timings = []
        for phase, secs in report.phase_times.items():
            mins = int(secs // 60)
            s = int(secs % 60)
            timings.append(f"{phase}={mins}m{s:02d}s" if mins else f"{phase}={s}s")
        table.add_row("Phase Timings", f"[{DIM}]{', '.join(timings)}[/{DIM}]")

    total_secs = sum(report.phase_times.values())
    total_mins = int(total_secs // 60)
    total_s = int(total_secs % 60)
    table.add_row("Total Time", f"{total_mins}m {total_s}s")

    if report.errors:
        for err in report.errors[:5]:
            table.add_row(
                "Error",
                f"[{ERROR}]{err[:100]}[/{ERROR}]",
            )

    border = STATUS_BORDER if not report.errors else WARNING_BORDER
    panel = Panel(
        table,
        title="[bold]Daemon Session Report[/bold]",
        border_style=border,
        padding=(1, 1),
    )
    console.print(panel)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _display_duration(seconds: float) -> None:
    """Show elapsed time footer."""
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    if mins > 0:
        elapsed = f"{mins}m {secs}s"
    else:
        elapsed = f"{secs}s"
    console.print(f"  [{MUTED}]\u23f1 {elapsed}[/{MUTED}]")
