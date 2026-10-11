"use client";

import { WifiOff } from "lucide-react";

/** Shown by the service worker when a page is not cached and the network is down. */
export default function OfflinePage() {
  return (
    <div className="grid min-h-screen place-items-center bg-ink-950 p-6 text-center">
      <div className="max-w-sm">
        <div className="mx-auto grid h-16 w-16 place-items-center rounded-3xl bg-amber-500/10"><WifiOff className="h-8 w-8 text-amber-400" /></div>
        <h1 className="mt-5 text-xl font-bold text-white">You are offline</h1>
        <p className="mt-2 text-sm text-gray-500">Prices, orders and the kill switch need the network, so nothing here is shown from an old copy. The app reconnects by itself.</p>
        <button type="button" onClick={() => window.location.reload()} className="mt-6 rounded-xl bg-brand-500 px-5 py-2.5 text-sm font-semibold text-onaccent">Try again</button>
      </div>
    </div>
  );
}
