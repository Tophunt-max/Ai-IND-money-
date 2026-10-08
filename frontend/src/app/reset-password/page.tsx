"use client";

import { CheckCircle2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { AuthFrame } from "@/components/AuthGate";
import { Button, ErrorBox, Loading } from "@/components/ui";
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

  return (
    <AuthFrame>
      <h2 className="text-2xl font-semibold tracking-tight text-white">Set a new password</h2>
      <p className="mt-1 mb-6 text-sm text-gray-400">Choose at least 8 characters.</p>
      <ErrorBox error={error} />
      {done ? (
        <div className="flex items-center gap-2 text-sm text-emerald-300">
          <CheckCircle2 className="h-4 w-4" /> Password changed. Opening the dashboard...
        </div>
      ) : !ready ? (
        <>
          <Loading text="Checking the reset link..." />
          <p className="text-xs text-gray-500">If nothing happens, the link has expired: request a new one from the sign-in screen.</p>
        </>
      ) : (
        <form onSubmit={save} className="space-y-3">
          <div>
            <label className="label">New password</label>
            <input type="password" autoComplete="new-password" value={password}
              onChange={(e) => setPassword(e.target.value)} className="field" />
          </div>
          <div>
            <label className="label">Repeat it</label>
            <input type="password" autoComplete="new-password" value={again}
              onChange={(e) => setAgain(e.target.value)} className="field" />
          </div>
          <Button type="submit" size="lg" loading={busy} disabled={!password} className="w-full">Save password</Button>
        </form>
      )}
    </AuthFrame>
  );
}
