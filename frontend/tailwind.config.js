/** @type {import('tailwindcss').Config} */
// Modern app theme (light + dark). Every colour the app uses is a CSS variable set in
// globals.css for `:root` (light) and `.dark`, so one class list renders both themes:
//   ink-*   page and card surfaces          white   strongest text / hairline tints
//   gray-*  text steps (inverted in light)  brand   indigo accent
//   emerald / rose  up / down (P&L)          amber   warnings
// `onaccent` is always white: text on a coloured button or pill.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
const scale = (name, steps) => Object.fromEntries(steps.map((s) => [s, v(`${name}-${s}`)]));

module.exports = {
  darkMode: "class",
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-sans)", "Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Arial", "sans-serif"],
        mono: ["var(--font-mono)", "JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["10px", "14px"],
      },
      colors: {
        white: v("white"),
        onaccent: "#ffffff",
        ink: scale("ink", [950, 900, 850, 800, 700, 600]),
        gray: scale("gray", [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950]),
        brand: scale("brand", [50, 100, 200, 300, 400, 500, 600, 700]),
        emerald: scale("emerald", [100, 200, 300, 400, 500, 600]),
        rose: scale("rose", [100, 200, 300, 400, 500, 600]),
        amber: scale("amber", [100, 200, 300, 400, 500, 600]),
        up: v("emerald-500"),
        down: v("rose-500"),
      },
      borderRadius: {
        xl: "14px",
        "2xl": "18px",
        "3xl": "24px",
      },
      boxShadow: {
        card: "var(--shadow-card)",
        pop: "var(--shadow-pop)",
        glow: "0 0 0 1px rgb(var(--brand-500) / 0.45)",
        sheet: "0 -16px 48px -16px rgb(0 0 0 / 0.35)",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0", transform: "translateY(4px)" }, to: { opacity: "1", transform: "none" } },
        "scale-in": { from: { opacity: "0", transform: "scale(.97)" }, to: { opacity: "1", transform: "none" } },
        shimmer: { "100%": { transform: "translateX(100%)" } },
        "slide-up": { from: { transform: "translateY(100%)" }, to: { transform: "none" } },
        "slide-left": { from: { transform: "translateX(100%)" }, to: { transform: "none" } },
        ticker: { from: { transform: "translateX(0)" }, to: { transform: "translateX(-50%)" } },
        "flash-up": { "0%": { backgroundColor: "rgb(var(--emerald-500) / 0.28)" }, "100%": { backgroundColor: "transparent" } },
        "flash-down": { "0%": { backgroundColor: "rgb(var(--rose-500) / 0.28)" }, "100%": { backgroundColor: "transparent" } },
      },
      animation: {
        "fade-in": "fade-in .25s ease-out both",
        "scale-in": "scale-in .18s ease-out both",
        shimmer: "shimmer 1.6s infinite",
        "slide-up": "slide-up .28s cubic-bezier(.2,.8,.2,1) both",
        "slide-left": "slide-left .28s cubic-bezier(.2,.8,.2,1) both",
        ticker: "ticker 40s linear infinite",
        "flash-up": "flash-up 1s ease-out",
        "flash-down": "flash-down 1s ease-out",
      },
    },
  },
  plugins: [],
};
