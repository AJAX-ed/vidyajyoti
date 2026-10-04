import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Frontend MUST listen on port 3000 (global rule).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": "/src" },
  },
  server: {
    host: "0.0.0.0",
    port: 3000,
    allowedHosts: "all",
  },
});
