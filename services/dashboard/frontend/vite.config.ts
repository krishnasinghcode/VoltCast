import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, /api and /ws are proxied to the API (default localhost:8000) so the browser stays same-origin.
// Set VITE_API_URL to bypass the proxy and call the API directly (CORS is enabled for :5173).
const target = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: { manualChunks: { react: ["react", "react-dom", "react-router-dom"], charts: ["recharts"] } },
    },
  },
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": { target, changeOrigin: true },
      "/ws": { target: target.replace(/^http/, "ws"), ws: true, changeOrigin: true },
    },
  },
});
