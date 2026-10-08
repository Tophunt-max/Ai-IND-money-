"use client";

import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import { api } from "@/lib/api";
import {
  clearLoginFailures,
  loginLockedFor,
  noteLoginFailure,
  rememberMe,
  setRememberMe,
  supabase,
  supabaseConfigured,
} from "@/lib/supabase";

import { BarChart3, Brain, Lock, Loader2, ShieldCheck } from "lucide-react";

import { Button, ErrorBox } from "./ui";

export interface DashUser {
  email: string;
  role: "admin" | "viewer";
  via: string;
  provider?: string | null;
  name?: string | null;
}

interface AuthState {
  user: DashUser | null;
  isAdmin: boolean;
  signOut: (everywhere?: boolean) => Promise<void>;
}

const AuthContext = createContext<AuthState>({
  user: null,
  isAdmin: false,
  signOut: async () => {},
});

export const useAuth = () => useContext(AuthContext);

/** Pages that work without a login (the link from the password-reset email). */
const PUBLIC_PATHS = ["/reset-password"];

export default function AuthGate({ children }: { children: ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [state, setState] = useState<"checking" | "login" | "denied" | "ok">("checking");
  const [user, setUser] = useState<DashUser | null>(null);
  const [error, setError] = useState<string | null>(null);

  const signOut = useCallback(async (everywhere = false) => {
    await supabase()?.auth.signOut({ scope: everywhere ? "global" : "local" });
    setUser(null);
    setState("login");
  }, []);

  const verify = useCallback(async () => {
    try {
      const me = await api<{ user: DashUser }>("/api/dashboard/me");
      setUser(me.user);
      setError(null);
      setState("ok");
    } catch (e: any) {
      if (e.status === 403) {
        setError(e.message);
        setState("denied");
      } else if (e.status === 401) {
        await supabase()?.auth.signOut({ scope: "local" });
        setState("login");
      } else {
        setError(e.message);
        setState("login");
      }
    }
  }, []);

  useEffect(() => {
    const sb = supabase();
    if (!sb) {
      setState("login");
      return;
    }
    sb.auth.getSession().then(({ data }) => (data.session ? verify() : setState("login")));
    const { data: sub } = sb.auth.onAuthStateChange((event, session) => {
      if (event === "PASSWORD_RECOVERY") {
        router.push("/reset-password");
        return;
      }
      if (event === "SIGNED_IN") verify();
      if (event === "SIGNED_OUT" || !session) {
        setUser(null);
        setState("login");
      }
    });
    const onUnauthorized = () => signOut(false);
    window.addEventListener("aiind:unauthorized", onUnauthorized);
    return () => {
      sub.subscription.unsubscribe();
      window.removeEventListener("aiind:unauthorized", onUnauthorized);
    };
  }, [router, signOut, verify]);

  if (PUBLIC_PATHS.some((p) => path.startsWith(p))) return <>{children}</>;

  if (state === "checking") {
    return (
      <div className="grid min-h-screen place-items-center">
        <div className="flex items-center gap-3 text-sm text-gray-400">
          <Loader2 className="h-5 w-5 animate-spin text-brand-400" /> Connecting...
        </div>
      </div>
    );
  }
  if (state === "denied") {
    return (
      <AuthFrame>
        <div className="text-center space-y-4">
          <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-rose-500/10 ring-1 ring-rose-500/25">
            <Lock className="h-6 w-6 text-rose-300" />
          </div>
          <h2 className="text-xl font-semibold text-white">Access denied</h2>
          <p className="text-sm text-gray-400">{error}</p>
          <Button onClick={() => signOut(false)} className="w-full">Use another account</Button>
        </div>
      </AuthFrame>
    );
  }
  if (state === "login") return <LoginScreen serverError={error} />;
  return (
    <AuthContext.Provider value={{ user, isAdmin: user?.role === "admin", signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

function LoginScreen({ serverError }: { serverError: string | null }) {
  const [mode, setMode] = useState<"login" | "forgot">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => setRemember(rememberMe()), []);

  if (!supabaseConfigured) {
    return (
      <AuthFrame>
        <h2 className="text-xl font-semibold text-white mb-2">Login not set up</h2>
        <p className="text-sm text-gray-400">
          Add <code className="text-brand-300">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
          <code className="text-brand-300">NEXT_PUBLIC_SUPABASE_ANON_KEY</code> in Vercel → Settings →
          Environment Variables, then redeploy.
        </p>
      </AuthFrame>
    );
  }
  const sb = supabase()!;

  const locked = () => {
    const ms = loginLockedFor();
    if (ms > 0) {
      setError(`Too many wrong passwords. Try again in ${Math.ceil(ms / 60000)} min.`);
      return true;
    }
    return false;
  };

  const login = async (e: FormEvent) => {
    e.preventDefault();
    if (locked()) return;
    setBusy(true);
    setError(null);
    setRememberMe(remember);
    const { error: err } = await sb.auth.signInWithPassword({ email: email.trim(), password });
    setBusy(false);
    if (err) {
      noteLoginFailure();
      if (!locked()) {
        setError(
          err.message === "Invalid login credentials" ? "Wrong email or password." : err.message,
        );
      }
      return;
    }
    clearLoginFailures();
  };

  const google = async () => {
    setError(null);
    setRememberMe(remember);
    const { error: err } = await sb.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: window.location.origin },
    });
    if (err) setError(err.message);
  };

  const forgot = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { error: err } = await sb.auth.resetPasswordForEmail(email.trim(), {
      redirectTo: `${window.location.origin}/reset-password`,
    });
    setBusy(false);
    if (err) setError(err.message);
    else setInfo("If this email has an account, a reset link is on its way. Check your inbox.");
  };

  return (
    <AuthFrame>
      <h2 className="text-2xl font-semibold tracking-tight text-white">
        {mode === "login" ? "Welcome back" : "Reset password"}
      </h2>
      <p className="mt-1 mb-6 text-sm text-gray-400">
        {mode === "login" ? "Sign in to your trading dashboard." : "We will email you a reset link."}
      </p>
      <ErrorBox error={error || serverError} />
      {info && <div className="mb-4 rounded-xl border border-emerald-500/25 bg-emerald-500/10 p-3 text-sm text-emerald-200">{info}</div>}

      {mode === "login" ? (
        <>
          <form onSubmit={login} className="space-y-3">
            <div>
              <label className="label">Email</label>
              <input type="email" autoComplete="email" required value={email}
                onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" className="field" />
            </div>
            <div>
              <label className="label">Password</label>
              <input type="password" autoComplete="current-password" required value={password}
                onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" className="field" />
            </div>
            <div className="flex items-center justify-between pt-1 text-sm">
              <label className="flex items-center gap-2 text-gray-400">
                <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)}
                  className="h-4 w-4 rounded border-white/20 bg-ink-850 accent-indigo-500" />
                Remember me
              </label>
              <button type="button" className="link"
                onClick={() => { setMode("forgot"); setError(null); setInfo(null); }}>
                Forgot password?
              </button>
            </div>
            <Button type="submit" size="lg" loading={busy} disabled={!email || !password} className="w-full">
              {busy ? "Signing in..." : "Sign in"}
            </Button>
          </form>
          <div className="my-5 flex items-center gap-3 text-[11px] uppercase tracking-wider text-gray-600">
            <div className="flex-1 border-t border-white/[0.06]" />or<div className="flex-1 border-t border-white/[0.06]" />
          </div>
          <Button variant="ghost" size="lg" onClick={google} className="w-full">
            <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden><path fill="#EA4335" d="M12 10.2v3.9h5.4c-.2 1.3-1.6 3.8-5.4 3.8-3.2 0-5.9-2.7-5.9-6s2.7-6 5.9-6c1.9 0 3.1.8 3.8 1.5l2.6-2.5C16.8 3.3 14.6 2.4 12 2.4 6.7 2.4 2.4 6.7 2.4 12s4.3 9.6 9.6 9.6c5.5 0 9.2-3.9 9.2-9.4 0-.6-.1-1.1-.2-1.6H12z"/></svg>
            Continue with Google
          </Button>
          <p className="mt-6 flex items-center gap-2 text-xs text-gray-500">
            <ShieldCheck className="h-3.5 w-3.5" /> Only accounts added by the admin can sign in.
          </p>
        </>
      ) : (
        <form onSubmit={forgot} className="space-y-3">
          <div>
            <label className="label">Email</label>
            <input type="email" autoComplete="email" required value={email}
              onChange={(e) => setEmail(e.target.value)} placeholder="Your account email" className="field" />
          </div>
          <Button type="submit" size="lg" loading={busy} disabled={!email} className="w-full">
            {busy ? "Sending..." : "Send reset link"}
          </Button>
          <button type="button" className="w-full text-sm text-gray-400 hover:text-gray-200"
            onClick={() => { setMode("login"); setError(null); setInfo(null); }}>
            ← Back to sign in
          </button>
        </form>
      )}
    </AuthFrame>
  );
}

/** Split screen: brand panel (desktop) + form card. */
export function AuthFrame({ children }: { children: ReactNode }) {
  const features = [
    { icon: Brain, title: "15-agent AI analysis", text: "Analysts, bull/bear debate, trader and risk team on every stock." },
    { icon: ShieldCheck, title: "Safety first", text: "Position limits, loss circuit breakers and a kill switch." },
    { icon: BarChart3, title: "Honest track record", text: "Every AI call scored against NIFTY." },
  ];
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="relative hidden overflow-hidden border-r border-white/[0.05] lg:flex lg:flex-col lg:justify-between p-12">
        <div className="absolute inset-0 bg-[radial-gradient(40rem_25rem_at_20%_20%,rgba(99,102,241,0.25),transparent_60%),radial-gradient(30rem_20rem_at_80%_90%,rgba(34,211,238,0.12),transparent_60%)]" />
        <div className="relative flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-brand-500 to-cyan-400 shadow-glow">
            <span className="text-sm font-black text-white">AI</span>
          </div>
          <span className="text-lg font-semibold text-white">AI IND Money</span>
        </div>
        <div className="relative max-w-md">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight text-white">
            AI trading for <span className="bg-gradient-to-r from-brand-300 to-cyan-300 bg-clip-text text-transparent">Indian markets</span>
          </h1>
          <p className="mt-4 text-gray-400">Multi-agent research, automated sessions and live broker integration, in one place.</p>
          <div className="mt-10 space-y-5">
            {features.map((f) => (
              <div key={f.title} className="flex gap-4">
                <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-white/[0.04] ring-1 ring-white/10">
                  <f.icon className="h-5 w-5 text-brand-300" />
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-100">{f.title}</div>
                  <div className="text-sm text-gray-500">{f.text}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="relative text-xs text-gray-600">NSE · BSE · INDstocks · Zerodha Kite</div>
      </div>
      <div className="flex items-center justify-center px-4 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <div className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-brand-500 to-cyan-400 shadow-glow">
              <span className="text-sm font-black text-white">AI</span>
            </div>
            <span className="text-lg font-semibold text-white">AI IND Money</span>
          </div>
          <div className="surface p-6 sm:p-8">{children}</div>
        </div>
      </div>
    </div>
  );
}
