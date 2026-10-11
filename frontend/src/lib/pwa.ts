"use client";

import { useSyncExternalStore } from "react";

// The PWA install prompt (Chrome / Edge / Android) and the "new version ready" state of the
// service worker, shared with Settings and the update banner.

interface PromptEvent extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> }

let deferred: PromptEvent | null = null;
let installed = false;
let waiting: ServiceWorker | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
let snap = { canInstall: false, installed: false, updateReady: false };

function refresh() {
  snap = { canInstall: !!deferred, installed, updateReady: !!waiting };
  emit();
}

export function capturePwaEvents() {
  if (typeof window === "undefined") return;
  installed = window.matchMedia?.("(display-mode: standalone)").matches || (navigator as any).standalone === true;
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferred = e as PromptEvent;
    refresh();
  });
  window.addEventListener("appinstalled", () => {
    deferred = null;
    installed = true;
    refresh();
  });
  refresh();
}

export function setWaitingWorker(w: ServiceWorker | null) {
  waiting = w;
  refresh();
}

export async function promptInstall(): Promise<string> {
  if (!deferred) return "unavailable";
  await deferred.prompt();
  const choice = await deferred.userChoice;
  deferred = null;
  refresh();
  return choice.outcome;
}

export function applyUpdate() {
  if (!waiting) return;
  waiting.postMessage({ type: "SKIP_WAITING" });
}

export function usePwa() {
  return useSyncExternalStore(
    (fn) => { listeners.add(fn); return () => listeners.delete(fn); },
    () => snap,
    () => snap,
  );
}
