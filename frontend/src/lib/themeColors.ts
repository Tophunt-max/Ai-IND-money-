"use client";

import { useEffect, useState } from "react";

// Chart colours for the theme on screen: the CSS variables (globals.css) as plain rgb()
// strings, re-read when the theme class on <html> changes. SVG attributes then get real
// colours (CSS variables in presentation attributes are not supported everywhere).

export interface ThemeColors {
  dark: boolean;
  up: string;
  down: string;
  accent: string;
  amber: string;
  violet: string;
  sky: string;
  text: string;
  muted: string;
  grid: string;
  gridSoft: string;
  axis: string;
  surface: string;
}

const LIGHT: ThemeColors = {
  dark: false, up: "rgb(0 179 134)", down: "rgb(235 91 60)", accent: "rgb(83 103 255)", amber: "rgb(214 118 6)",
  violet: "rgb(139 92 246)", sky: "rgb(14 165 233)", text: "rgb(16 22 34)", muted: "rgb(107 114 128)",
  grid: "rgb(16 22 34 / 0.07)", gridSoft: "rgb(16 22 34 / 0.04)", axis: "rgb(16 22 34 / 0.22)", surface: "rgb(255 255 255)",
};
const DARK: ThemeColors = {
  dark: true, up: "rgb(0 208 156)", down: "rgb(245 104 78)", accent: "rgb(123 136 255)", amber: "rgb(251 191 36)",
  violet: "rgb(167 139 250)", sky: "rgb(56 189 248)", text: "rgb(255 255 255)", muted: "rgb(107 114 128)",
  grid: "rgb(255 255 255 / 0.06)", gridSoft: "rgb(255 255 255 / 0.03)", axis: "rgb(255 255 255 / 0.25)", surface: "rgb(22 25 31)",
};

export function currentColors(): ThemeColors {
  if (typeof document === "undefined") return LIGHT;
  return document.documentElement.classList.contains("dark") ? DARK : LIGHT;
}

export function useThemeColors(): ThemeColors {
  const [c, setC] = useState<ThemeColors>(LIGHT);
  useEffect(() => {
    setC(currentColors());
    if (typeof MutationObserver === "undefined") return;
    const mo = new MutationObserver(() => setC(currentColors()));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => mo.disconnect();
  }, []);
  return c;
}

/** A palette for categories (allocation, sectors). */
export const PALETTE = ["#5367ff", "#00b386", "#f59e0b", "#a78bfa", "#ec4899", "#06b6d4", "#f97316", "#94a3b8", "#22c55e", "#ef4444"];
