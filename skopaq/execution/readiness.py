"""Live readiness: is this host ready to trade real money on INDstocks today?

One list of checks, shared by ``skopaq preflight``, the dashboard's Readiness card
(``GET /api/dashboard/readiness``), the scheduler's pre-flight alert and the daemon's
PRE_OPEN (which refuses a live session from an egress IP that is not whitelisted):

- **Token**: valid, and still valid at the end of today's session; automatic (TOTP) or
  not, and whether automatic generation is paused after failures.
- **Broker account** (``GET /user/profile``): NSE (and, with the F&O engine on, F&O)
  onboarded; DDPI active, without which a delivery SELL needs a TPIN the API cannot give.
- **Clock**: the host's against the broker's ``Date`` header. TOTP codes fail beyond
  about a minute, and market-hours checks run on this clock.
- **Static IP**: this host's public IPv4/IPv6 against ``SKOPAQ_INDSTOCKS_STATIC_IPS``, the
  IPs whitelisted on the INDstocks Access Tokens page. The broker refuses orders from any
  other IP (SEBI/NSE: static IPs for API orders; a slot can change once a week).
- **Funds**, the **kill switch**, the **WebSocket budget** (3 connections per account),
  the **control directory** (dashboard), and **Supabase** (trade rows; loss limits across
  sessions).

Each check is ``ok``, ``warn`` or ``fail``. Live, a ``fail`` means orders would be refused
or unsafe; paper only needs market data, so most live failures are warnings there.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_END = time(15, 45)            # the scheduler's default hard stop
CLOCK_WARN_S = 5.0
CLOCK_FAIL_S = 30.0
WS_CONNECTIONS_PER_ACCOUNT = 3
_EGRESS_URLS = {"ipv4": "https://api.ipify.org?format=json",
                "ipv6": "https://api6.ipify.org?format=json"}


@dataclass
class Check:
    name: str
    status: str                 # ok | warn | fail
    detail: str
    fix: str = ""


@dataclass
class Readiness:
    live: bool
    checks: list[Check] = field(default_factory=list)
    checked_at: str = ""

    @property
    def passed(self) -> bool:
        return not any(c.status == "fail" for c in self.checks)

    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == "fail"]

    def as_dict(self) -> dict:
        return {"live": self.live, "passed": self.passed, "checked_at": self.checked_at,
                "checks": [asdict(c) for c in self.checks]}

    def summary(self) -> str:
        bad = [c for c in self.checks if c.status != "ok"]
        if not bad:
            return "ready: every check passed"
        return "; ".join(f"{c.status.upper()} {c.name}: {c.detail}" for c in bad)

    def add(self, name: str, status: str, detail: str, fix: str = "") -> Check:
        check = Check(name, status, detail, fix)
        self.checks.append(check)
        return check


# ── Static IPs ───────────────────────────────────────────────────────────────


def static_ips(config: Any) -> list[str]:
    """The whitelisted IPs from ``SKOPAQ_INDSTOCKS_STATIC_IPS`` (normalised; bad entries
    are dropped with a warning)."""
    raw = getattr(config, "indstocks_static_ips", "") or ""
    if not isinstance(raw, str):
        return []
    out = []
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(str(ipaddress.ip_address(item)))
        except ValueError:
            logger.warning("SKOPAQ_INDSTOCKS_STATIC_IPS: %r is not an IP address", item)
    return out


async def egress_ips(*, timeout: float = 5.0, transport: Any = None) -> dict:
    """This host's public IPv4 and IPv6 (None when it has none / the lookup failed)."""
    import httpx

    async def one(url: str) -> Optional[str]:
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=transport) as http:
                resp = await http.get(url)
            ip = str(resp.json().get("ip", "")).strip()
            return str(ipaddress.ip_address(ip)) if ip else None
        except Exception:
            return None

    v4, v6 = await asyncio.gather(one(_EGRESS_URLS["ipv4"]), one(_EGRESS_URLS["ipv6"]))
    return {"ipv4": v4, "ipv6": v6}


def egress_verdict(egress: dict, allowed: list[str]) -> tuple[Optional[bool], str]:
    """(True: an egress IP is whitelisted, False: none is, None: unknown), and why.

    The broker sees the IPv6 address when the host reaches it over IPv6, else the IPv4
    one, so either being whitelisted counts."""
    seen = [ip for ip in (egress.get("ipv4"), egress.get("ipv6")) if ip]
    if not seen:
        return None, "this host's public IP could not be found (ipify unreachable)"
    if not allowed:
        return None, f"egress {', '.join(seen)}; SKOPAQ_INDSTOCKS_STATIC_IPS is not set"
    matched = [ip for ip in seen if ip in allowed]
    if matched:
        return True, f"egress {', '.join(seen)} — {', '.join(matched)} is whitelisted"
    return False, (f"egress {', '.join(seen)} is not among the whitelisted "
                   f"{', '.join(allowed)}")


# ── The checks ───────────────────────────────────────────────────────────────


def session_end(now: datetime) -> datetime:
    day = now.astimezone(IST).date()
    end = datetime.combine(day, SESSION_END, tzinfo=IST)
    # After today's session: the next one (tomorrow's) is what the token must carry
    return end if now.astimezone(IST) < end else end + timedelta(days=1)


def _check_token(r: Readiness, config: Any, now: datetime) -> bool:
    from skopaq.broker import auto_token
    from skopaq.broker.token_manager import TokenManager, session_token_problem

    health = TokenManager().get_health(notify=False)
    auto = auto_token.configured(config)
    gen = auto_token.generation_state()
    how = "automatic (TOTP)" if auto else "set by hand each day"
    if not health.valid:
        r.add("token", "fail", f"no valid INDstocks token ({health.warning}); {how}",
              "skopaq token auto" if auto else
              "skopaq token set <TOKEN>, or set up TOTP (docs/deployment/go-live.md)")
        return False
    problem = session_token_problem(health, session_end(now))
    if problem:
        r.add("token", "fail" if not auto else "warn", problem + f"; {how}",
              "the scheduler makes a new one before the session" if auto
              else "skopaq token set <TOKEN>")
    else:
        expires = health.expires_at.astimezone(IST).strftime("%d %b %H:%M IST") \
            if health.expires_at else "unknown"
        r.add("token", "ok", f"valid until {expires}; {how}")
    if gen.get("paused_until"):
        r.add("auto token", "fail", f"generation paused after failures: {gen['last_error']}",
              "check SKOPAQ_INDSTOCKS_MPIN, SKOPAQ_INDSTOCKS_TOTP_SECRET and the host clock")
    return True


async def _check_account(r: Readiness, config: Any, client: Any,
                         wall: Callable[[], datetime], live: bool) -> None:
    bad = "fail" if live else "warn"
    try:
        profile = await client.get_profile()
    except Exception as exc:
        r.add("broker", "fail", f"GET /user/profile failed: {exc}",
              "check the token; INDstocks may be down")
        return
    extra = getattr(profile, "model_extra", None) or {}

    def flag(name: str) -> Optional[bool]:
        value = extra.get(name)
        return value if isinstance(value, bool) else None

    who = profile.name or profile.email or profile.user_id or "account"
    ucc = extra.get("ucc") or ""
    r.add("broker", "ok", f"{who}" + (f" (UCC {ucc})" if ucc else ""))
    if flag("is_nse_onboarded") is False:
        r.add("NSE segment", bad, "the account is not onboarded on NSE",
              "activate NSE on INDmoney")
    if getattr(config, "fno_enabled", False) is True:
        fno = flag("is_nse_fno_onboarded")
        if fno is False:
            r.add("F&O segment", bad, "the F&O engine is on but NSE F&O is not activated",
                  "activate F&O on INDmoney (income proof), or SKOPAQ_FNO_ENABLED=false")
        elif fno:
            r.add("F&O segment", "ok", "NSE F&O activated")
    if flag("is_ddpi_active") is False:
        r.add("DDPI", "warn", "DDPI is not active: delivery (CNC) SELLs of holdings need a "
              "TPIN the API cannot give, so swing exits of earlier days' shares may be "
              "refused", "activate DDPI on INDmoney (intraday and F&O are not affected)")
    elif flag("is_ddpi_active"):
        r.add("DDPI", "ok", "DDPI active (delivery SELLs need no TPIN)")

    server = getattr(client, "server_date", None)
    if isinstance(server, datetime):
        # The Date header has whole seconds: up to 1 s of the drift is that
        drift = (wall() - server).total_seconds()
        status = "ok" if abs(drift) <= CLOCK_WARN_S else (
            "warn" if abs(drift) <= CLOCK_FAIL_S else "fail")
        r.add("clock", status, f"host clock {drift:+.0f}s from the broker's",
              "" if status == "ok" else "enable NTP (chrony / systemd-timesyncd)")

    try:
        funds = await client.get_funds()
    except Exception as exc:
        r.add("funds", "warn", f"funds unreadable: {exc}")
        return
    cash = float(funds.available_cash or 0)
    detail = f"₹{cash:,.0f} available for equity"
    if getattr(config, "fno_enabled", False) is True:
        detail += f", ₹{float(funds.option_buy_available or 0):,.0f} for option buying"
    r.add("funds", "ok" if cash > 0 else ("warn" if not live else "fail"), detail,
          "" if cash > 0 else "add funds on INDmoney")


async def _check_egress(r: Readiness, config: Any, live: bool,
                        egress: Optional[dict], transport: Any) -> None:
    allowed = static_ips(config)
    egress = egress if egress is not None else await egress_ips(transport=transport)
    verdict, detail = egress_verdict(egress, allowed)
    if verdict is True:
        r.add("static IP", "ok", detail)
    elif verdict is False:
        r.add("static IP", "fail" if live else "warn", detail,
              "whitelist this IP on indstocks.com/app/api-trading/access-tokens (a slot "
              "can change once a week), or use the host with the Elastic IP")
    else:
        r.add("static IP", "warn", detail,
              "set SKOPAQ_INDSTOCKS_STATIC_IPS to the IPs whitelisted on INDstocks "
              "(the EC2 Elastic IP)")


def _check_local(r: Readiness, config: Any) -> None:
    from skopaq.execution import kill_switch
    from skopaq.execution.control import ControlChannel

    halt = kill_switch.status(use_cache=False)
    if halt.halted:
        r.add("kill switch", "warn", f"{halt.describe()} — no new BUYs",
              "skopaq resume (or Resume on the Control page)")
    else:
        r.add("kill switch", "ok", "off (BUYs allowed)")

    feeds = sum(getattr(config, name, False) is True
                for name in ("ws_price_feed_enabled", "ws_order_feed_enabled"))
    r.add("websockets", "ok" if feeds <= WS_CONNECTIONS_PER_ACCOUNT else "warn",
          f"a session opens {feeds} connection(s) (one shared price feed); INDstocks allows "
          f"{WS_CONNECTIONS_PER_ACCOUNT} per account — `skopaq ticks` while a session "
          "runs takes one more")

    channel = ControlChannel.from_config(config)
    if channel is None:
        r.add("control dir", "warn", "SKOPAQ_CONTROL_DIR is not set: the dashboard cannot "
              "see or control sessions", "set it on the shared home volume")
    else:
        try:
            channel.dir.mkdir(parents=True, exist_ok=True)
            probe = channel.dir / ".readiness-probe"
            probe.write_text("ok")
            probe.unlink()
            r.add("control dir", "ok", str(channel.dir))
        except OSError as exc:
            r.add("control dir", "warn", f"{channel.dir} is not writable: {exc}")

    supabase = bool(getattr(config, "supabase_url", "")) and bool(
        _secret(getattr(config, "supabase_service_key", "")))
    r.add("database", "ok" if supabase else "warn",
          "Supabase configured" if supabase else "Supabase not configured: no trade rows, "
          "and the loss limits count only the running session's trades")


def _secret(value: Any) -> str:
    getter = getattr(value, "get_secret_value", None)
    return getter() if callable(getter) else str(value or "")


async def check_readiness(
    config: Any,
    *,
    live: Optional[bool] = None,
    client: Any = None,
    egress: Optional[dict] = None,
    transport: Any = None,
    wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Readiness:
    """Every check (never raises). ``live``: judge for live trading (default: the
    configured mode)."""
    live = (getattr(config, "trading_mode", "paper") == "live") if live is None else live
    now = wall()
    r = Readiness(live=live, checked_at=now.astimezone(IST).isoformat(timespec="seconds"))
    r.add("mode", "ok", "LIVE: real orders" if live else "paper: simulated orders")
    try:
        token_ok = _check_token(r, config, now)
    except Exception as exc:
        token_ok = False
        r.add("token", "fail", f"token unreadable: {exc}")
    tasks = [_check_egress(r, config, live, egress, transport)]
    own = None
    if token_ok:
        if client is None:
            from skopaq.broker.client import INDstocksClient
            from skopaq.broker.token_manager import TokenManager

            own = INDstocksClient(config, TokenManager())
            client = await own.__aenter__()
        tasks.append(_check_account(r, config, client, wall, live))
    try:
        await asyncio.gather(*tasks)
    except Exception as exc:                        # a check's own bug: still answer
        logger.exception("Readiness check failed")
        r.add("readiness", "warn", f"a check failed: {exc}")
    finally:
        if own is not None:
            await own.__aexit__(None, None, None)
    try:
        _check_local(r, config)
    except Exception as exc:
        r.add("local", "warn", f"local checks failed: {exc}")
    r.checks.sort(key=lambda c: _ORDER.index(c.name) if c.name in _ORDER else len(_ORDER))
    return r


_ORDER = ["mode", "token", "auto token", "broker", "NSE segment", "F&O segment", "DDPI",
          "clock", "static IP", "funds", "kill switch", "websockets", "control dir",
          "database"]


async def live_egress_problem(config: Any, *, transport: Any = None) -> str:
    """Why a live session must not start from this host ("" when it may): the static
    IPs are set and none of this host's public IPs is among them. An unknown egress IP
    (lookup failed) is not a reason: the broker will say if it refuses."""
    allowed = static_ips(config)
    if not allowed:
        return ""
    verdict, detail = egress_verdict(await egress_ips(transport=transport), allowed)
    if verdict is False:
        return (f"{detail}: INDstocks refuses orders from it. Whitelist it on the Access "
                "Tokens page (once a week) or run on the host with the whitelisted IP")
    return ""
