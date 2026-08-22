import type { NextConfig } from "next";

// Standalone output keeps the runtime image small; the host runs it behind the
// same reverse proxy as Django (§3.2 — one host, :3000 and :8000).
const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
