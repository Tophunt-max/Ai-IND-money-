"use client";

// Supabase Auth for the dashboard login (email + password, Google, password reset).
// "Remember me" picks where the session lives: localStorage (stays logged in on this
// device, tokens refresh automatically) or sessionStorage (logged out when the tab closes).

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const URL = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const ANON = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";
const REMEMBER_KEY = "aiind_remember";

export const supabaseConfigured = Boolean(URL && ANON);

export function rememberMe(): boolean {
  if (typeof window === "undefined") return true;
  return window.localStorage.getItem(REMEMBER_KEY) !== "0";
}

export function setRememberMe(value: boolean): void {
  window.localStorage.setItem(REMEMBER_KEY, value ? "1" : "0");
}

const storage = {
  getItem: (k: string) => window.localStorage.getItem(k) ?? window.sessionStorage.getItem(k),
  setItem: (k: string, v: string) => {
    if (rememberMe()) {
      window.localStorage.setItem(k, v);
      window.sessionStorage.removeItem(k);
    } else {
      window.sessionStorage.setItem(k, v);
      window.localStorage.removeItem(k);
    }
  },
  removeItem: (k: string) => {
    window.localStorage.removeItem(k);
    window.sessionStorage.removeItem(k);
  },
};

let client: SupabaseClient | null = null;

/** The browser Supabase client (null on the server or when not configured). */
export function supabase(): SupabaseClient | null {
  if (typeof window === "undefined" || !supabaseConfigured) return null;
  if (!client) {
    client = createClient(URL, ANON, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, storage },
    });
  }
  return client;
}

/** The current access token (refreshed by supabase-js when it is about to expire). */
export async function accessToken(): Promise<string> {
  const sb = supabase();
  if (!sb) return "";
  const { data } = await sb.auth.getSession();
  return data.session?.access_token || "";
}

// ── Client-side brake on password guessing (Supabase also rate-limits) ─────────
const FAIL_KEY = "aiind_login_failures";
const MAX_FAILS = 5;
const LOCK_MS = 5 * 60 * 1000;

export function loginLockedFor(): number {
  try {
    const s = JSON.parse(window.localStorage.getItem(FAIL_KEY) || "{}");
    return s.until && s.until > Date.now() ? s.until - Date.now() : 0;
  } catch {
    return 0;
  }
}

export function noteLoginFailure(): void {
  let s: { count?: number; until?: number } = {};
  try {
    s = JSON.parse(window.localStorage.getItem(FAIL_KEY) || "{}");
  } catch {}
  const count = (s.count || 0) + 1;
  window.localStorage.setItem(
    FAIL_KEY,
    JSON.stringify(count >= MAX_FAILS ? { count: 0, until: Date.now() + LOCK_MS } : { count }),
  );
}

export function clearLoginFailures(): void {
  window.localStorage.removeItem(FAIL_KEY);
}
