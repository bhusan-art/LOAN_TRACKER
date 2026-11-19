import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

// The built app goes into the Python package; FastAPI serves it (see web/app.py serve_spa).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../loan_tracker/web/spa", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 900 },
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000", "/export.csv": "http://localhost:8000", "/static": "http://localhost:8000" },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
