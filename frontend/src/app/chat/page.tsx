"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button, ErrorBox, PageTitle } from "@/components/ui";
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
        "/api/chat/message",
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
    <div className="flex flex-col h-[calc(100vh-12rem)] md:h-[calc(100vh-9rem)]">
      <PageTitle title="AI chat" right={<Button variant="ghost" onClick={reset}>New chat</Button>} />
      <p className="text-xs text-gray-500 -mt-3 mb-3">
        Paper mode: trades the AI makes here are simulated.
      </p>

      <div className="flex-1 overflow-y-auto space-y-3 pr-1">
        {messages.length === 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => send(s)}
                className="border border-gray-800 rounded-lg p-3 text-sm text-left text-gray-300 hover:bg-gray-900"
              >
                {s}
              </button>
            ))}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                m.role === "user" ? "bg-blue-600 text-white" : "bg-gray-900 border border-gray-800 text-gray-200"
              }`}
            >
              {m.text}
              {m.tools && m.tools.length > 0 && (
                <div className="text-[10px] text-gray-500 mt-2">
                  🔧 {m.tools.map((t) => t.tool).join(", ")}
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && <div className="animate-pulse text-gray-500 text-sm">AI is thinking...</div>}
        <div ref={bottom} />
      </div>

      <ErrorBox error={error} />
      <form onSubmit={submit} className="flex gap-2 pt-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about a stock, the market or your portfolio..."
          className="flex-1 bg-gray-900 border border-gray-700 rounded-md px-3 py-2 text-sm"
        />
        <Button type="submit" disabled={busy || !input.trim()}>Send</Button>
      </form>
    </div>
  );
}
