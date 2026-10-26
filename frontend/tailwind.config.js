/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        sev: { normal: "#16a34a", info: "#2563eb", warning: "#d97706", alarm: "#dc2626", stale: "#6b7280" },
        shell: { bg: "#0f172a", panel: "#111827", border: "#1f2937" },
      },
    },
  },
  plugins: [],
}
