"use client";

import { Bot, Loader2, MessagesSquare, Plus, Send, Sparkles, Wrench } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { useAuth } from "@/components/AuthGate";
import { Button, cx, ErrorBox, Notice, PageTitle } from "@/components/ui";
import { api } from "@/lib/api";

interface Msg {
  role: "user" | "ai";
  text: string;
  tools?: { tool: string }[];
}

const SESSION_KEY = "aiind_chat_session";
const HISTORY_KEY = "aiind_chat_history";
const SUGGESTIONS = [
  "What should I trade today?",
  "Quote RELIANCE",
  "Show my portfolio",
  "Is the market bullish right now?",
];

export default function ChatPage() {
  const { isAdmin } = useAuth();
  if (!isAdmin) {
    return (
      <div>
        <PageTitle title="AI chat" icon={MessagesSquare} />
        <Notice tone="neutral">View-only account: the AI chat can place (paper) trades, so it needs an admin account.</Notice>
      </div>
    );
  }
  return <Chat />;
}

function Chat() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      setMessages(JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "[]"));
    } catch {}
  }, []);

  useEffect(() => {
    sessionStorage.setItem(HISTORY_KEY, JSON.stringify(messages.slice(-50)));
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const send = async (text: string) => {
    const msg = text.trim();
    if (!msg || busy) return;
    setInput("");
    setError(null);
    setMessages((m) => [...m, { role: "user", text: msg }]);
    setBusy(true);
    try {
      const res = await api<{ message: string; session_id: string; tool_calls: { tool: string }[] }>(
        "/api/dashboard/chat",
        { method: "POST", json: { message: msg, session_id: sessionStorage.getItem(SESSION_KEY) || "" } },
      );
      sessionStorage.setItem(SESSION_KEY, res.session_id);
      setMessages((m) => [...m, { role: "ai", text: res.message, tools: res.tool_calls }]);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    send(input);
  };

  const reset = () => {
    sessionStorage.removeItem(SESSION_KEY);
    sessionStorage.removeItem(HISTORY_KEY);
    setMessages([]);
  };

  return (
    <div className="flex h-[calc(100dvh-13rem)] flex-col lg:h-[calc(100dvh-6rem)]">
      <PageTitle title="AI chat" icon={MessagesSquare} subtitle="Ask about stocks, the market or your portfolio. Trades here are paper."
        right={<Button variant="ghost" icon={Plus} onClick={reset}>New chat</Button>} />

      <div className="surface flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="flex-1 space-y-4 overflow-y-auto p-4 md:p-6">
          {messages.length === 0 && (
            <div className="flex h-full flex-col items-center justify-center gap-6 text-center">
              <div className="grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-brand-500 to-cyan-400 shadow-glow">
                <Sparkles className="h-6 w-6 text-white" />
              </div>
              <div>
                <div className="text-lg font-semibold text-white">How can I help today?</div>
                <div className="text-sm text-gray-500">The assistant can analyze, quote, scan and check your portfolio.</div>
              </div>
              <div className="grid w-full max-w-xl grid-cols-1 gap-2 sm:grid-cols-2">
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => send(s)}
                    className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-left text-sm text-gray-300 transition hover:border-brand-500/30 hover:text-white">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={cx("flex gap-3", m.role === "user" ? "justify-end" : "justify-start")}>
              {m.role === "ai" && (
                <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-brand-500/30 to-cyan-400/20 ring-1 ring-white/10">
                  <Bot className="h-4 w-4 text-brand-200" />
                </div>
              )}
              <div className={cx("max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
                m.role === "user" ? "rounded-br-md bg-gradient-to-b from-brand-500 to-brand-600 text-white" : "rounded-bl-md bg-white/[0.04] text-gray-200 ring-1 ring-white/[0.06]")}>
                {m.text}
                {m.tools && m.tools.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {m.tools.map((t, k) => (
                      <span key={k} className="inline-flex items-center gap-1 rounded-md bg-black/30 px-1.5 py-0.5 text-[10px] text-gray-400">
                        <Wrench className="h-3 w-3" />{t.tool}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
          {busy && (
            <div className="flex items-center gap-2 text-sm text-gray-500">
              <Loader2 className="h-4 w-4 animate-spin text-brand-400" /> Thinking...
            </div>
          )}
          <div ref={bottom} />
        </div>

        <div className="border-t border-white/[0.06] p-3">
          <ErrorBox error={error} />
          <form onSubmit={submit} className="relative">
            <input value={input} onChange={(e) => setInput(e.target.value)}
              placeholder="Message the AI..." className="field h-12 pr-14" />
            <button type="submit" disabled={busy || !input.trim()}
              className="absolute right-2 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-lg bg-gradient-to-b from-brand-500 to-brand-600 text-white transition disabled:opacity-40">
              <Send className="h-4 w-4" />
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
