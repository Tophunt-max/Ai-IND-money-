"""PPO, A2C and DQN trading agents (PyTorch), trained and judged in ``rl_env``.

FreqAI drives stable-baselines3 for this. These are compact native implementations of
the same three algorithms, so a policy is a plain ``torch`` module whose weights are
saved with ``safetensors`` (no pickle, verified by SHA-256 like every bundle) and whose
behaviour is fully visible here:

* **PPO** — clipped surrogate objective, GAE(λ), several epochs of minibatches per rollout.
* **A2C** — synchronous advantage actor-critic with n-step returns.
* **DQN** — double DQN with a replay buffer, a target network and ε-greedy exploration.

All three respect the action mask (no "exit" while flat, no "enter" while long), use
the session's net-of-charges equity change as the reward, and are seeded.

``train_policy`` learns on the *earlier* sessions only; ``evaluate_policy`` replays later,
unseen sessions greedily and reports the trades in the registry's gate format (net,
profit factor, bootstrap CI of the expectancy, drawdown) against a buy-and-hold baseline.
``train_and_register`` stores the result as a ``candidate`` in the RL registry
(``<model_dir>/rl``): it reaches ``shadow`` only by passing the gates and ``champion``
only after the shadow days and a named admin approval — see ``rl_live``.

**Continual learning** (``RLConfig.continual``, ``SKOPAQ_RL_CONTINUAL_LEARNING``,
``--continual``): training starts from the previous policy of the same algorithm (RL
champion, else shadow, else the newest — ``find_parent``) with its weights and its
observation scaling kept, instead of from random weights; DQN explores less
(``continual_eps_start``). It is still a candidate behind the same gates.

Nothing here places orders.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from skopaq.adaptive import artifacts
from skopaq.adaptive.rl_env import (
    ACTIONS,
    FEATURES,
    OBS_SIZE,
    STATE,
    EnvConfig,
    RLTradingEnv,
    precompute,
)
from skopaq.finance.charges import EQUITY_INTRADAY, leg_charges
from skopaq.learning.evaluation import trade_stats
from skopaq.learning.registry import Gates, ModelRegistry

logger = logging.getLogger(__name__)

ALGOS = ("ppo", "a2c", "dqn")
RL_KIND = "rl-policy-v1"
# The RL registry's gates: the model gates without the probability-calibration check
# (a policy outputs actions, not a probability of a labelled outcome)
RL_GATES = Gates(require_better_brier=False)


def torch_available() -> bool:
    try:
        import safetensors  # noqa: F401
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass(frozen=True)
class RLConfig:
    algo: str = "ppo"
    timesteps: int = 60_000
    seed: int = 7
    hidden: int = 64
    gamma: float = 0.99
    lr: float = 3e-4                  # PPO and DQN
    a2c_lr: float = 7e-4              # A2C's smaller, more frequent updates
    train_frac: float = 0.7
    # PPO / A2C
    n_steps: int = 1024
    gae_lambda: float = 0.95
    clip: float = 0.2
    epochs: int = 4
    batch: int = 256
    entropy: float = 0.01
    value_coef: float = 0.5
    # DQN
    buffer: int = 50_000
    learn_start: int = 1_000
    target_sync: int = 500
    eps_start: float = 1.0
    eps_end: float = 0.05
    eps_frac: float = 0.6
    continual: bool = False
    continual_eps_start: float = 0.2      # DQN: a continued policy explores less
    env: EnvConfig = field(default_factory=EnvConfig)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["env"] = self.env.as_dict()
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RLConfig":
        d = dict(d)
        env = EnvConfig.from_dict(d.pop("env", {}) or {})
        return cls(env=env, **{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── networks ─────────────────────────────────────────────────────────────────


def build_net(algo: str, hidden: int):
    import torch.nn as nn

    class ActorCritic(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.body = nn.Sequential(nn.Linear(OBS_SIZE, hidden), nn.Tanh(),
                                      nn.Linear(hidden, hidden), nn.Tanh())
            self.pi = nn.Linear(hidden, len(ACTIONS))
            self.v = nn.Linear(hidden, 1)

        def forward(self, x):
            z = self.body(x)
            return self.pi(z), self.v(z).squeeze(-1)

    class QNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.q = nn.Sequential(nn.Linear(OBS_SIZE, hidden), nn.ReLU(),
                                   nn.Linear(hidden, hidden), nn.ReLU(),
                                   nn.Linear(hidden, len(ACTIONS)))

        def forward(self, x):
            return self.q(x), None

    if algo not in ALGOS:
        raise ValueError(f"unknown RL algorithm {algo!r} (one of {', '.join(ALGOS)})")
    return QNet() if algo == "dqn" else ActorCritic()


class Normalizer:
    """Feature scaling fitted on the training sessions (state columns pass through)."""

    def __init__(self, mean: np.ndarray, std: np.ndarray) -> None:
        self.mean, self.std = np.asarray(mean, float), np.asarray(std, float)

    @classmethod
    def fit(cls, sessions: Sequence[tuple[list, np.ndarray]]) -> "Normalizer":
        X = np.concatenate([f for _, f in sessions]) if sessions else np.zeros((1, len(FEATURES)))
        std = X.std(axis=0)
        return cls(X.mean(axis=0), np.where(std > 1e-9, std, 1.0))

    def __call__(self, obs: np.ndarray) -> np.ndarray:
        obs = np.array(obs, dtype=np.float32)
        k = len(FEATURES)
        obs[..., :k] = np.clip((obs[..., :k] - self.mean) / self.std, -8, 8)
        return obs

    def as_dict(self) -> dict:
        return {"mean": self.mean.tolist(), "std": self.std.tolist()}


def masked_logits(logits, mask):
    import torch
    return logits.masked_fill(~mask, torch.finfo(logits.dtype).min)


def greedy_action(net, x: np.ndarray, mask: np.ndarray) -> tuple[int, list[float]]:
    """The deterministic action and the per-action scores (probabilities or Q-values)."""
    import torch
    with torch.no_grad():
        out, _ = net(torch.as_tensor(x[None], dtype=torch.float32))
        m = torch.as_tensor(mask[None])
        scores = masked_logits(out, m)
        a = int(scores.argmax(-1)[0])
        if type(net).__name__ == "QNet":
            vals = [float(v) if ok else float("nan") for v, ok in zip(out[0].tolist(), mask)]
        else:
            vals = torch.softmax(scores, -1)[0].tolist()
    return a, vals


# ── session sampler ──────────────────────────────────────────────────────────


class _Runner:
    """Cycles through the training sessions in a seeded random order."""

    def __init__(self, sessions, cfg: RLConfig, norm: Normalizer, rng: random.Random):
        self.sessions, self.cfg, self.norm, self.rng = sessions, cfg, norm, rng
        self.order: list[int] = []
        self.env: Optional[RLTradingEnv] = None
        self.obs = None
        self.episode_net: list[float] = []
        self._new()

    def _new(self) -> None:
        if not self.order:
            self.order = list(range(len(self.sessions)))
            self.rng.shuffle(self.order)
        day, feats = self.sessions[self.order.pop()]
        self.env = RLTradingEnv(day, self.cfg.env, feats)
        self.obs = self.norm(self.env.reset())
        if self.env.done:
            self._new()

    def step(self, a: int):
        env = self.env
        assert env is not None
        nxt, r, done = env.step(a)
        if done:
            self.episode_net.append(env.net)
            self._new()
            return self.obs, r, True
        self.obs = self.norm(nxt)
        return self.obs, r, False

    def mask(self) -> np.ndarray:
        assert self.env is not None
        return self.env.mask()


# ── training ─────────────────────────────────────────────────────────────────


def _seed(seed: int) -> random.Random:
    import torch
    torch.manual_seed(seed)
    np.random.seed(seed)
    return random.Random(seed)


def _train_actor_critic(net, runner: _Runner, cfg: RLConfig) -> dict:
    import torch
    opt = torch.optim.Adam(net.parameters(), lr=cfg.lr if cfg.algo == "ppo" else cfg.a2c_lr)
    n_steps = cfg.n_steps if cfg.algo == "ppo" else 32
    steps, updates = 0, 0
    while steps < cfg.timesteps:
        obs, acts, logps, rews, dones, vals, masks = [], [], [], [], [], [], []
        for _ in range(n_steps):
            x = torch.as_tensor(runner.obs[None], dtype=torch.float32)
            m = torch.as_tensor(runner.mask()[None])
            with torch.no_grad():
                logits, v = net(x)
                dist = torch.distributions.Categorical(logits=masked_logits(logits, m))
                a = dist.sample()
            obs.append(runner.obs)
            masks.append(runner.mask())
            acts.append(int(a))
            logps.append(float(dist.log_prob(a)))
            vals.append(float(v))
            _, r, done = runner.step(int(a))
            rews.append(r)
            dones.append(done)
            steps += 1
        with torch.no_grad():
            _, last_v = net(torch.as_tensor(runner.obs[None], dtype=torch.float32))
        adv = np.zeros(len(rews), np.float32)
        gae, nv = 0.0, float(last_v)
        lam = cfg.gae_lambda if cfg.algo == "ppo" else 1.0
        for i in reversed(range(len(rews))):
            nonterm = 0.0 if dones[i] else 1.0
            delta = rews[i] + cfg.gamma * nv * nonterm - vals[i]
            gae = delta + cfg.gamma * lam * nonterm * gae
            adv[i] = gae
            nv = vals[i]
        ret = adv + np.asarray(vals, np.float32)
        X = torch.as_tensor(np.asarray(obs), dtype=torch.float32)
        M = torch.as_tensor(np.asarray(masks))
        A = torch.as_tensor(acts)
        LP = torch.as_tensor(logps, dtype=torch.float32)
        ADV = torch.as_tensor(adv)
        RET = torch.as_tensor(ret)
        epochs = cfg.epochs if cfg.algo == "ppo" else 1
        batch = cfg.batch if cfg.algo == "ppo" else len(acts)
        for _ in range(epochs):
            idx = torch.randperm(len(acts))
            for s in range(0, len(acts), batch):
                b = idx[s:s + batch]
                logits, v = net(X[b])
                dist = torch.distributions.Categorical(logits=masked_logits(logits, M[b]))
                lp = dist.log_prob(A[b])
                adv_b = ADV[b]
                if cfg.algo == "ppo":
                    adv_b = (adv_b - adv_b.mean()) / (adv_b.std() + 1e-8)
                    ratio = torch.exp(lp - LP[b])
                    pg = -torch.min(ratio * adv_b,
                                    torch.clamp(ratio, 1 - cfg.clip, 1 + cfg.clip) * adv_b).mean()
                else:
                    pg = -(lp * adv_b).mean()
                loss = pg + cfg.value_coef * ((RET[b] - v) ** 2).mean() \
                    - cfg.entropy * dist.entropy().mean()
                opt.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(), 0.5)
                opt.step()
                updates += 1
    return {"steps": steps, "updates": updates}


def _train_dqn(net, runner: _Runner, cfg: RLConfig) -> dict:
    import copy

    import torch
    opt = torch.optim.Adam(net.parameters(), lr=cfg.lr)
    target = copy.deepcopy(net)
    cap = cfg.buffer
    S = np.zeros((cap, OBS_SIZE), np.float32)
    S2 = np.zeros((cap, OBS_SIZE), np.float32)
    A = np.zeros(cap, np.int64)
    R = np.zeros(cap, np.float32)
    D = np.zeros(cap, np.float32)
    M2 = np.zeros((cap, len(ACTIONS)), bool)
    n = ptr = updates = 0
    rng = np.random.default_rng(cfg.seed)
    for step in range(cfg.timesteps):
        frac = min(1.0, step / max(1, cfg.eps_frac * cfg.timesteps))
        eps = cfg.eps_start + frac * (cfg.eps_end - cfg.eps_start)
        mask = runner.mask()
        o = runner.obs
        if rng.random() < eps:
            a = int(rng.choice(np.flatnonzero(mask)))
        else:
            a, _ = greedy_action(net, o, mask)
        o2, r, done = runner.step(a)
        S[ptr], A[ptr], R[ptr], D[ptr], S2[ptr] = o, a, r, float(done), o2
        M2[ptr] = runner.mask()
        ptr, n = (ptr + 1) % cap, min(n + 1, cap)
        if n >= cfg.learn_start and step % 4 == 0:
            b = rng.integers(0, n, size=min(cfg.batch, n))
            s, s2 = torch.as_tensor(S[b]), torch.as_tensor(S2[b])
            q = net(s)[0].gather(1, torch.as_tensor(A[b])[:, None]).squeeze(1)
            with torch.no_grad():
                m2 = torch.as_tensor(M2[b])
                a2 = masked_logits(net(s2)[0], m2).argmax(1, keepdim=True)   # double DQN
                q2 = target(s2)[0].gather(1, a2).squeeze(1)
                y = torch.as_tensor(R[b]) + cfg.gamma * (1 - torch.as_tensor(D[b])) * q2
            loss = torch.nn.functional.smooth_l1_loss(q, y)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), 10.0)
            opt.step()
            updates += 1
        if step % cfg.target_sync == 0:
            target.load_state_dict(net.state_dict())
    return {"steps": cfg.timesteps, "updates": updates}


def train_policy(sessions, cfg: RLConfig, init: Any = None):
    """``(net, normalizer, info)`` trained on ``sessions`` (``precompute`` output).
    ``init``: a loaded ``rl_live.RLPolicy`` to continue (continual learning)."""
    from dataclasses import replace

    if not torch_available():
        raise RuntimeError("RL agents need PyTorch: pip install 'skopaq[torch]'")
    if not sessions:
        raise ValueError("no training sessions with enough bars")
    rng = _seed(cfg.seed)
    net = build_net(cfg.algo, cfg.hidden)
    if init is not None:
        net.load_state_dict(init.net.state_dict())
        norm = init.norm                     # the input scaling the weights were trained on
        if cfg.algo == "dqn":
            cfg = replace(cfg, eps_start=min(cfg.eps_start, cfg.continual_eps_start))
    else:
        norm = Normalizer.fit(sessions)
    runner = _Runner(sessions, cfg, norm, rng)
    info = (_train_dqn if cfg.algo == "dqn" else _train_actor_critic)(net, runner, cfg)
    info["continued_from"] = getattr(init, "version", None)
    net.eval()
    eps = runner.episode_net
    info["episodes"] = len(eps)
    info["train_net_last_20"] = round(float(np.mean(eps[-20:])), 2) if eps else None
    return net, norm, info


# ── evaluation ───────────────────────────────────────────────────────────────


def run_session(net, norm: Normalizer, day, feats, env_cfg: EnvConfig) -> RLTradingEnv:
    env = RLTradingEnv(day, env_cfg, feats)
    obs = env.reset()
    while not env.done:
        a, _ = greedy_action(net, norm(obs), env.mask())
        obs, _, _ = env.step(a)
    return env


def _buy_hold(day, env_cfg: EnvConfig) -> float:
    i = min(env_cfg.warmup + 1, len(day) - 1)
    fill = day[i].open * (1 + env_cfg.slippage_pct)
    qty = max(1, int(env_cfg.notional // fill))
    out = day[-1].close * (1 - env_cfg.slippage_pct)
    return ((out - fill) * qty - leg_charges(EQUITY_INTRADAY, "BUY", fill * qty).total
            - leg_charges(EQUITY_INTRADAY, "SELL", out * qty).total)


def evaluate_policy(net, norm: Normalizer, sessions, env_cfg: EnvConfig) -> dict:
    """Greedy replay of unseen sessions, in the registry's report format."""
    pnls, per_day, exits, steps, charges, refused = [], [], {}, 0, 0.0, 0
    actions = {a: 0 for a in ACTIONS}
    for day, feats in sessions:
        env = RLTradingEnv(day, env_cfg, feats)
        obs = env.reset()
        while not env.done:
            a, _ = greedy_action(net, norm(obs), env.mask())
            actions[ACTIONS[a]] += 1
            obs, _, _ = env.step(a)
            steps += 1
        pnls += env.trade_pnls
        per_day.append(round(env.net, 2))
        charges += env.charges
        refused += env.refused
        for k, v in env.exits.items():
            exits[k] = exits.get(k, 0) + v
    hold = [_buy_hold(d, env_cfg) for d, _ in sessions]
    model = trade_stats(pnls).as_dict()
    model["charges"] = round(charges, 2)
    base = trade_stats(hold).as_dict()
    return {"samples": steps, "model": model, "baseline": base,
            "baseline_name": "buy at the open after warm-up, sell at the close",
            "baseline_flat": 0.0, "per_session_net": per_day, "exits": exits,
            "actions": actions, "refused_on_cost": refused,
            "brier": None, "brier_base_rate": None, "leakage": []}


# ── saving ───────────────────────────────────────────────────────────────────


def save_policy(net, norm: Normalizer, cfg: RLConfig, directory: Path, *, name: str,
                extra: Mapping[str, Any]) -> Path:
    from safetensors.torch import save_file

    d = Path(directory) / name
    d.mkdir(parents=True, exist_ok=False)
    save_file({k: v.detach().contiguous() for k, v in net.state_dict().items()},
              str(d / "policy.safetensors"))
    meta = {"kind": RL_KIND, "algo": cfg.algo, "hidden": cfg.hidden, "features": FEATURES,
            "state": STATE, "actions": ACTIONS, "normalizer": norm.as_dict(),
            "config": cfg.as_dict(), "timeframe_s": 60, **extra}
    (d / "policy.json").write_text(json.dumps(meta, indent=1))
    artifacts.write_manifest(d, version=name, extra={"format_kind": RL_KIND,
                                                     "algo": cfg.algo})
    return d


def rl_registry(model_root: Path) -> ModelRegistry:
    return ModelRegistry(Path(model_root).expanduser() / "rl")


def _fingerprint(history: Mapping[str, Sequence[Sequence[Any]]]) -> str:
    h = hashlib.sha256()
    for sym in sorted(history):
        for day in history[sym]:
            if day:
                h.update(f"{sym}{day[0].start.isoformat()}{len(day)}{day[-1].close}".encode())
    return h.hexdigest()[:16]


def split_sessions(history: Mapping[str, Sequence[Sequence[Any]]], train_frac: float):
    """Train on every symbol's earlier sessions, test on its later ones (by date)."""
    train, test = [], []
    for sym in sorted(history):
        sess = precompute(history[sym])
        n = max(1, int(len(sess) * train_frac))
        train += sess[:n]
        test += sess[n:]
    return train, test


def find_parent(registry: Optional[ModelRegistry], cfg: RLConfig) -> tuple[Any, str]:
    """``(policy, why)``: the policy a continual run continues (verified on load), or
    None and the reason."""
    if registry is None:
        return None, "no registry"
    from skopaq.adaptive.rl_live import RLPolicy

    entries = [registry.champion(), registry.shadow()]
    entries += [registry.get(v["version"]) for v in reversed(registry.versions())]
    why = "no previous policy"
    for e in entries:
        if not e or (e.get("model") or {}).get("kind") != RL_KIND:
            continue
        if e["model"].get("algo") != cfg.algo:
            why = f"no previous {cfg.algo.upper()} policy"
            continue
        try:
            p = RLPolicy(e["model"]["path"], version=e["version"])
        except Exception as exc:
            why = f"{e['version']} not usable: {exc}"
            continue
        if int(p.meta.get("hidden", -1)) != cfg.hidden:
            why = f"{e['version']} has another network size"
            continue
        return p, ""
    return None, why


def train_and_register(history: Mapping[str, Sequence[Sequence[Any]]], cfg: RLConfig, *,
                       registry: Optional[ModelRegistry] = None) -> dict:
    """Train, evaluate out of sample, save and (with a registry) register + gate."""
    train, test = split_sessions(history, cfg.train_frac)
    if not test:
        raise ValueError("not enough sessions: some must be kept back for the test")
    t0 = datetime.now(timezone.utc)
    parent, why = find_parent(registry, cfg) if cfg.continual else (None, "off")
    net, norm, info = train_policy(train, cfg, init=parent)
    report = evaluate_policy(net, norm, test, cfg.env)
    report["continual"] = {"enabled": cfg.continual,
                           "parent": parent.version if parent is not None else None,
                           **({"fresh": why} if cfg.continual and parent is None else {})}
    report.update(algo=cfg.algo, train_sessions=len(train), test_sessions=len(test),
                  training=info, seconds=round((datetime.now(timezone.utc) - t0)
                                               .total_seconds(), 1))
    out: dict[str, Any] = {"report": report}
    if registry is None:
        out["policy"] = (net, norm)
        return out
    name = f"rl-{cfg.algo}-{t0.strftime('%Y%m%dT%H%M%S')}-{random.Random().getrandbits(24):06x}"
    dataset = {"symbols": sorted(history), "fingerprint": _fingerprint(history),
               "train_sessions": len(train), "test_sessions": len(test)}
    path = save_policy(net, norm, cfg, registry.dir / "artifacts", name=name,
                       extra={"dataset": dataset, "trained_at": t0.isoformat(),
                              "continued_from": report["continual"]["parent"]})
    entry = registry.register(model={"kind": RL_KIND, "bundle": name, "path": str(path),
                                     "algo": cfg.algo},
                              report=report, params={"rl": cfg.as_dict()}, dataset=dataset,
                              strategy_id=f"rl.{cfg.algo}",
                              notes=f"{cfg.algo.upper()} on {', '.join(sorted(history))}"
                              + (f", continued from {report['continual']['parent']}"
                                 if report["continual"]["parent"] else ""))
    fails = registry.evaluate_candidate(entry["version"], gates=RL_GATES)
    out.update(version=entry["version"], bundle=name, path=str(path), gate_failures=fails,
               state="shadow" if not fails else "candidate")
    return out


# ── config ───────────────────────────────────────────────────────────────────


def config_from(config: Any, **over: Any) -> RLConfig:
    """``RLConfig`` from ``SkopaqConfig``: the scalper's live geometry and limits, so the
    policy trains under the rules it will trade under."""
    from skopaq.scalping.settings import ScalpSettings

    s = ScalpSettings.from_config(config)
    env = EnvConfig(notional=s.max_position_value, slippage_pct=s.qualify_rules.slippage_pct,
                    rr=s.params.rr, stop_atr=float(getattr(config, "rl_stop_atr", 1.5) or 1.5),
                    max_hold_minutes=s.max_hold.total_seconds() / 60,
                    entry_start=s.entry_start, entry_end=s.entry_end, flatten_at=s.flatten_at,
                    min_reward_to_cost=s.min_reward_to_cost,
                    min_net_rr=s.qualify_rules.min_net_rr)
    algo = str(over.pop("algo", None) or getattr(config, "rl_algo", "ppo") or "ppo").lower()
    cont = over.pop("continual", None)
    over["continual"] = (getattr(config, "rl_continual_learning", False) is True
                         if cont is None else bool(cont))
    steps = int(over.pop("timesteps", None) or getattr(config, "rl_timesteps", 60_000))
    return RLConfig(algo=algo, timesteps=steps, env=env, **over)


def symbols_from(config: Any) -> list[str]:
    raw = (getattr(config, "rl_symbols", "") or getattr(config, "adaptive_symbols", "")
           or getattr(config, "scalp_symbols", "") or "")
    return [s.strip().upper() for s in str(raw).split(",") if s.strip()][:20]
