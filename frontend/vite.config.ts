import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, forward /api to the FastAPI container so the browser sees one origin (no CORS setup).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
