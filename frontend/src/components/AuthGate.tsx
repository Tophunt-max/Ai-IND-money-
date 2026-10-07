"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { api, BACKEND_URL, clearToken, getToken, setToken } from "@/lib/api";

import { Button, ErrorBox } from "./ui";

/** Shows a login screen until the saved SKOPAQ_API_TOKEN is accepted by the backend. */
export default function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"checking" | "login" | "ok">("checking");
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const check = async (token: string) => {
    await api("/api/dashboard/me", { token });
  };

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setState("login");
      return;
    }
    check(token)
      .then(() => setState("ok"))
      .catch((e) => {
        if (e.status === 401) clearToken();
        setError(e.status === 401 ? null : e.message);
        setState("login");
      });
    const onUnauthorized = () => {
      clearToken();
      setState("login");
    };
    window.addEventListener("aiind:unauthorized", onUnauthorized);
    return () => window.removeEventListener("aiind:unauthorized", onUnauthorized);
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const token = input.trim();
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await check(token);
      setToken(token);
      setState("ok");
    } catch (err: any) {
      setError(
        err.status === 401
          ? "Wrong password (SKOPAQ_API_TOKEN)."
          : err.status === 503
            ? "Dashboard is off on the server: add SKOPAQ_API_TOKEN to ENV_FILE and redeploy."
            : err.message,
      );
    } finally {
      setBusy(false);
    }
  };

  if (state === "checking") {
    return <div className="animate-pulse text-gray-400 py-10 text-center">Connecting...</div>;
  }
  if (state === "login") {
    return (
      <div className="max-w-sm mx-auto mt-10">
        <h2 className="text-2xl font-bold mb-2">Login</h2>
        <p className="text-sm text-gray-400 mb-5">
          Enter the dashboard password: the <code>SKOPAQ_API_TOKEN</code> value from your
          ENV_FILE secret. It is saved on this device only.
        </p>
        <ErrorBox error={error} />
        <form onSubmit={submit} className="space-y-3">
          <input
            type="password"
            autoComplete="current-password"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="SKOPAQ_API_TOKEN"
            className="w-full bg-gray-900 border border-gray-700 rounded-md px-3 py-2 text-sm"
          />
          <Button type="submit" disabled={busy || !input.trim()} className="w-full">
            {busy ? "Checking..." : "Login"}
          </Button>
        </form>
        <p className="text-xs text-gray-600 mt-6 break-all">Backend: {BACKEND_URL}</p>
      </div>
    );
  }
  return <>{children}</>;
}
