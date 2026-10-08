"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAuth } from "./AuthGate";

export const LINKS = [
  { href: "/", label: "Home", icon: "🏠" },
  { href: "/market", label: "Market", icon: "📈" },
  { href: "/analyze", label: "Analyze", icon: "🧠" },
  { href: "/trades", label: "Trades", icon: "📒" },
  { href: "/report", label: "Report", icon: "📊" },
  { href: "/chat", label: "Chat", icon: "💬" },
  { href: "/settings", label: "Settings", icon: "⚙️" },
];

export default function Nav() {
  const path = usePathname();
  const { user, signOut } = useAuth();
  if (path.startsWith("/reset-password")) return null;
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));

  return (
    <>
      <nav className="border-b border-gray-800 px-4 md:px-6 py-3 sticky top-0 bg-gray-950/95 backdrop-blur z-20">
        <div className="max-w-6xl mx-auto flex items-center justify-between gap-4">
          <Link href="/" className="text-xl font-bold tracking-tight shrink-0">
            AI IND <span className="text-blue-400">Money</span>
          </Link>
          <div className="hidden md:flex items-center gap-1 text-sm">
            {LINKS.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className={`px-3 py-1.5 rounded-md ${
                  active(l.href) ? "bg-gray-800 text-white" : "text-gray-400 hover:text-white"
                }`}
              >
                {l.label}
              </Link>
            ))}
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {user?.role === "viewer" && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-800 text-gray-300">VIEW ONLY</span>
            )}
            <button onClick={() => signOut(false)} className="text-xs text-gray-500 hover:text-gray-300">
              Logout
            </button>
          </div>
        </div>
      </nav>
      {/* Mobile: bottom tab bar */}
      <div className="md:hidden fixed bottom-0 inset-x-0 border-t border-gray-800 bg-gray-950/95 backdrop-blur z-20">
        <div className="grid grid-cols-7">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={`flex flex-col items-center py-2 text-[10px] ${
                active(l.href) ? "text-blue-400" : "text-gray-500"
              }`}
            >
              <span className="text-base leading-none">{l.icon}</span>
              <span className="mt-1">{l.label}</span>
            </Link>
          ))}
        </div>
      </div>
    </>
  );
}
