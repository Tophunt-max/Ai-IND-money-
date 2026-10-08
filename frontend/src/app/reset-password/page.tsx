"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button, ErrorBox } from "@/components/ui";
import { supabase } from "@/lib/supabase";

/** Opened from the password-reset email: Supabase signs the user in from the link. */
export default function ResetPasswordPage() {
  const [ready, setReady] = useState(false);
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    const sb = supabase();
    if (!sb) return;
    sb.auth.getSession().then(({ data }) => data.session && setReady(true));
    const { data: sub } = sb.auth.onAuthStateChange((_e, session) => session && setReady(true));
    const t = setTimeout(() => setReady((r) => r || false), 4000);
    return () => {
      sub.subscription.unsubscribe();
      clearTimeout(t);
    };
  }, []);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (password.length < 8) return setError("Use at least 8 characters.");
    if (password !== again) return setError("The two passwords do not match.");
    setBusy(true);
    setError(null);
    const { error: err } = await supabase()!.auth.updateUser({ password });
    setBusy(false);
    if (err) return setError(err.message);
    setDone(true);
    setTimeout(() => (window.location.href = "/"), 1500);
  };

  const input = "w-full bg-gray-900 border border-gray-700 rounded-md px-3 py-2 text-sm";
  return (
    <div className="max-w-sm mx-auto mt-10 px-4">
      <h2 className="text-2xl font-bold mb-4">Set a new password</h2>
      <ErrorBox error={error} />
      {done ? (
        <p className="text-green-400 text-sm">Password changed. Opening the dashboard...</p>
      ) : !ready ? (
        <p className="text-sm text-gray-400">
          Checking the reset link... If nothing happens, the link has expired: request a new one
          from the login screen.
        </p>
      ) : (
        <form onSubmit={save} className="space-y-3">
          <input type="password" autoComplete="new-password" value={password}
            onChange={(e) => setPassword(e.target.value)} placeholder="New password" className={input} />
          <input type="password" autoComplete="new-password" value={again}
            onChange={(e) => setAgain(e.target.value)} placeholder="New password again" className={input} />
          <Button type="submit" disabled={busy || !password} className="w-full">
            {busy ? "Saving..." : "Save password"}
          </Button>
        </form>
      )}
    </div>
  );
}
