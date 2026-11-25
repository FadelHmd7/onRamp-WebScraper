/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI runs on Vite (http://localhost:5173) and /api goes to
// the API on its published port. In Docker, nginx does the same (ui/nginx.conf),
// so the browser always talks to one origin and the API needs no CORS setup.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.API_URL ?? "http://localhost:9060",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
