import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The production server must mount these assets separately from the learning UI.
// No bearer credential or owner identity is injected by this development proxy.
export default defineConfig({
  plugins: [react()],
  base: "/wiki/",
  server: {
    host: "127.0.0.1",
    port: 5184,
    strictPort: true,
    proxy: {
      "/api/knowledge/v1": {
        target: process.env.WIKI_DEV_API_ORIGIN || "http://127.0.0.1:8766",
        changeOrigin: false,
      },
      "/health": {
        target: process.env.WIKI_DEV_API_ORIGIN || "http://127.0.0.1:8766",
        changeOrigin: false,
      },
    },
  },
  build: { target: "es2022", outDir: "dist", sourcemap: false, manifest: true },
});
