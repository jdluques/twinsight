import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo, proxyea la API y el WebSocket al backend local.
const BACKEND = process.env.TWINSIGHT_BACKEND || "http://127.0.0.1:8010";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
      "/ws": { target: BACKEND, ws: true, changeOrigin: true },
    },
  },
  build: { outDir: "dist" },
});
