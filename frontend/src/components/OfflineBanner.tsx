"use client";

import { WifiOff } from "lucide-react";
import { useEffect, useState } from "react";

/** A banner while the browser is offline: data on screen may be stale until it reconnects. */
export default function OfflineBanner() {
  const [offline, setOffline] = useState(false);
  useEffect(() => {
    const update = () => setOffline(typeof navigator !== "undefined" && !navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  if (!offline) return null;
  return (
    <div role="status" className="sticky top-0 z-50 flex items-center justify-center gap-2 bg-amber-500/90 px-4 py-1.5 text-xs font-medium text-ink-950">
      <WifiOff className="h-3.5 w-3.5" /> Offline: prices, positions and orders on screen may be stale. They refresh when the connection is back.
    </div>
  );
}
