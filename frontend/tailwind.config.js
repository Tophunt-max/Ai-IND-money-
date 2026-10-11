/** @type {import('tailwindcss').Config} */
// Trading-terminal theme: near-black panels, one blue accent, exchange green / red.
// emerald and rose are re-pointed to the up / down colours so every P&L class in the app
// (text-emerald-400, bg-rose-500/10, …) follows the theme.
module.exports = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto",
          "Helvetica Neue", "Arial", "sans-serif",
        ],
        mono: ["JetBrains Mono", "Roboto Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      fontSize: {
        "2xs": ["10px", "14px"],
      },
      colors: {
        ink: {
          950: "#0b0e11",
          900: "#12161c",
          850: "#161a21",
          800: "#1c2129",
          700: "#262c35",
          600: "#353c47",
        },
        brand: {
          200: "#c7d6ff",
          300: "#9db8ff",
          400: "#5b8cff",
          500: "#2f6bff",
          600: "#1f55e0",
        },
        emerald: {
          200: "#a7f3d0",
          300: "#3ee0a1",
          400: "#0ecb81",
          500: "#0ecb81",
          600: "#09a56a",
        },
        rose: {
          200: "#fecdd3",
          300: "#ff7186",
          400: "#f6465d",
          500: "#f6465d",
          600: "#d33148",
        },
        up: "#0ecb81",
        down: "#f6465d",
      },
      boxShadow: {
        card: "0 1px 0 0 rgba(255,255,255,0.03) inset",
        glow: "0 0 0 1px rgba(47,107,255,0.45)",
        sheet: "0 -12px 40px -12px rgba(0,0,0,0.8)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0", transform: "translateY(3px)" }, to: { opacity: "1", transform: "none" } },
        shimmer: { "100%": { transform: "translateX(100%)" } },
        "slide-up": { from: { transform: "translateY(100%)" }, to: { transform: "none" } },
        ticker: { from: { transform: "translateX(0)" }, to: { transform: "translateX(-50%)" } },
      },
      animation: {
        "fade-in": "fade-in .2s ease-out both",
        shimmer: "shimmer 1.6s infinite",
        "slide-up": "slide-up .22s ease-out both",
        ticker: "ticker 40s linear infinite",
      },
    },
  },
  plugins: [],
};
