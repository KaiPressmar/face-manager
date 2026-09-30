import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const currentDir = path.dirname(fileURLToPath(import.meta.url));
const appVersion = fs
  .readFileSync(path.resolve(currentDir, "../VERSION"), "utf8")
  .trim();

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    host: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
    watch: {
      ignored: [
        "**/dist/**",
        "**/build/**",
        "**/.git/**",
        "**/.mypy_cache/**",
        "**/.pytest_cache/**",
        "**/__pycache__/**",
        "**/backend/**",
        "**/packaging/**",
        "**/scripts/**",
      ],
    },
  },
});
