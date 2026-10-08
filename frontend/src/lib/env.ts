// Server settings saved from the dashboard (/api/dashboard/settings/env, admin only).

import { api, ApiError } from "./api";

export interface Setting {
  key: string;
  kind: "bool" | "int" | "float" | "choice" | "text";
  secret: boolean;
  choices: string[];
  default: string;
  group: string;
  help: string;
  locked: string;
  source: "dashboard" | "server" | "default";
  is_set: boolean;
  value: string | null;
  server_set: boolean;
}

export interface HistoryEntry {
  at: string;
  by: string;
  set: Record<string, string>;
  removed: string[];
  live: string[];
}

export interface EnvData {
  settings: Setting[];
  file: string;
  history: HistoryEntry[];
  changed?: string[];
  removed?: string[];
  live?: string[];
}

export interface EnvChange {
  set?: Record<string, string>;
  remove?: string[];
  confirm_live?: boolean;
}

export const LIVE_SETTINGS = {
  SKOPAQ_TRADING_MODE: "live",
  SKOPAQ_SCHEDULER_MODE: "live",
  SKOPAQ_SCHEDULER_CONFIRM_LIVE: "true",
};

export const PAPER_SETTINGS = {
  SKOPAQ_TRADING_MODE: "paper",
  SKOPAQ_SCHEDULER_MODE: "paper",
  SKOPAQ_SCHEDULER_CONFIRM_LIVE: "false",
};

export function saveEnv(body: EnvChange): Promise<EnvData> {
  return api<EnvData>("/api/dashboard/settings/env", { method: "POST", json: body });
}

/** Save; on 409 (the change turns real money on) ask to type LIVE and send it again. */
export async function saveEnvAsking(body: EnvChange): Promise<EnvData | null> {
  try {
    return await saveEnv(body);
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 409) throw e;
    const typed = prompt(
      `⚠️ REAL MONEY\n\n${e.message}\n\nThe scheduler will place real orders on INDstocks from ` +
        "its next session, without supervision.\n\nType LIVE to confirm:",
    );
    if ((typed || "").trim() !== "LIVE") return null;
    return await saveEnv({ ...body, confirm_live: true });
  }
}

/** The effective trading and scheduler modes from the settings list. */
export function modes(settings: Setting[]) {
  const v = (k: string) => settings.find((s) => s.key === k)?.value || "";
  const trading = v("SKOPAQ_TRADING_MODE") || "paper";
  const scheduler = v("SKOPAQ_SCHEDULER_MODE") || "paper";
  const confirmed = v("SKOPAQ_SCHEDULER_CONFIRM_LIVE") === "true";
  return { trading, scheduler, confirmed, live: trading === "live" || (scheduler === "live" && confirmed) };
}
