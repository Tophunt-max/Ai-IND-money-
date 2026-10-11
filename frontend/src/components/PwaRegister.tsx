"use client";

import { RefreshCw } from "lucide-react";
import { useEffect } from "react";

import { applyUpdate, capturePwaEvents, setWaitingWorker, usePwa } from "@/lib/pwa";

/** Registers the service worker (installable app, offline page) and offers an update
 * when a new version of the dashboard is waiting. */
export default function PwaRegister() {
  const pwa = usePwa();
  useEffect(() => {
    capturePwaEvents();
    if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;
    if (process.env.NODE_ENV !== "production") return;
    let reloading = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (reloading) return;
      reloading = true;
      window.location.reload();
    });
    navigator.serviceWorker.register("/sw.js").then((reg) => {
      if (reg.waiting && navigator.serviceWorker.controller) setWaitingWorker(reg.waiting);
      reg.addEventListener("updatefound", () => {
        const nw = reg.installing;
        nw?.addEventListener("statechange", () => {
          if (nw.state === "installed" && navigator.serviceWorker.controller) setWaitingWorker(nw);
        });
      });
    }).catch(() => {});
  }, []);
  if (!pwa.updateReady) return null;
  return (
    <div className="fixed inset-x-3 bottom-[calc(5rem+env(safe-area-inset-bottom))] z-[55] mx-auto flex max-w-md items-center gap-3 rounded-2xl border border-white/[0.08] bg-ink-900 p-3 shadow-pop animate-scale-in lg:bottom-4">
      <RefreshCw className="h-5 w-5 shrink-0 text-brand-400" />
      <div className="min-w-0 flex-1 text-sm"><div className="font-semibold text-white">A new version is ready</div><div className="text-xs text-gray-500">Reload to use it.</div></div>
      <button type="button" onClick={applyUpdate} className="rounded-xl bg-brand-500 px-3 py-1.5 text-sm font-semibold text-onaccent">Reload</button>
    </div>
  );
}
