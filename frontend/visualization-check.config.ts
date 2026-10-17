import { defineConfig } from "vite";
import base from "./vite.config";

// Isolated production-component fixture. Never included in the application build.
export default defineConfig({
  ...base,
  build: {
    ...base.build,
    outDir: process.env.VISUALIZATION_OUT_DIR ?? "../docs/visualization-evidence-2026-10-15/check-dist",
    rollupOptions: {
      ...base.build?.rollupOptions,
      input: process.env.VISUALIZATION_ENTRY ?? ["visualization-audit.html", "project-recovery.html"],
    },
  },
});
