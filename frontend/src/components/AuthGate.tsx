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
    return <div className="animate-pulse text-gray-400 py-10 text-center">Connecting...</div>;
  }
  if (state === "denied") {
    return (
      <div className="max-w-sm mx-auto mt-16 text-center space-y-4 px-4">
        <div className="text-4xl">🔒</div>
        <h2 className="text-xl font-bold">Access denied</h2>
        <p className="text-sm text-gray-400">{error}</p>
        <Button onClick={() => signOut(false)} className="w-full">Use another account</Button>
      </div>
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
      <div className="max-w-sm mx-auto mt-10 px-4">
        <h2 className="text-2xl font-bold mb-2">Login not set up</h2>
        <p className="text-sm text-gray-400">
          Add <code>NEXT_PUBLIC_SUPABASE_URL</code> and <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code> in
          Vercel → Settings → Environment Variables, then redeploy.
        </p>
      </div>
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

  const input = "w-full bg-gray-900 border border-gray-700 rounded-md px-3 py-2 text-sm";
  return (
    <div className="max-w-sm mx-auto mt-10 px-4">
      <h1 className="text-xl font-bold mb-6">
        AI IND <span className="text-blue-400">Money</span>
      </h1>
      <h2 className="text-2xl font-bold mb-4">{mode === "login" ? "Login" : "Reset password"}</h2>
      <ErrorBox error={error || serverError} />
      {info && <div className="text-sm text-green-400 mb-4">{info}</div>}

      {mode === "login" ? (
        <>
          <form onSubmit={login} className="space-y-3">
            <input type="email" autoComplete="email" required value={email}
              onChange={(e) => setEmail(e.target.value)} placeholder="Email" className={input} />
            <input type="password" autoComplete="current-password" required value={password}
              onChange={(e) => setPassword(e.target.value)} placeholder="Password" className={input} />
            <div className="flex items-center justify-between text-sm">
              <label className="flex items-center gap-2 text-gray-400">
                <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
                Remember me
              </label>
              <button type="button" className="text-blue-400"
                onClick={() => { setMode("forgot"); setError(null); setInfo(null); }}>
                Forgot password?
              </button>
            </div>
            <Button type="submit" disabled={busy || !email || !password} className="w-full">
              {busy ? "Logging in..." : "Login"}
            </Button>
          </form>
          <div className="flex items-center gap-3 my-5 text-xs text-gray-600">
            <div className="flex-1 border-t border-gray-800" />OR<div className="flex-1 border-t border-gray-800" />
          </div>
          <Button variant="ghost" onClick={google} className="w-full">
            <span className="mr-2 font-bold">G</span> Continue with Google
          </Button>
          <p className="text-xs text-gray-600 mt-6">
            Only accounts added by the admin can open the dashboard.
          </p>
        </>
      ) : (
        <form onSubmit={forgot} className="space-y-3">
          <input type="email" autoComplete="email" required value={email}
            onChange={(e) => setEmail(e.target.value)} placeholder="Your account email" className={input} />
          <Button type="submit" disabled={busy || !email} className="w-full">
            {busy ? "Sending..." : "Send reset link"}
          </Button>
          <button type="button" className="text-sm text-gray-400 w-full"
            onClick={() => { setMode("login"); setError(null); setInfo(null); }}>
            ← Back to login
          </button>
        </form>
      )}
    </div>
  );
}
