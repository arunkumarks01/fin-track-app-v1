import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

import { requireHttpUrl } from "./src/lib/env";

// Fail closed: no backend URL, no build.
const backendUrl = requireHttpUrl("BACKEND_URL");

const nextConfig = (phase: string): NextConfig => ({
  // The dev server and production builds can run at the same time.
  distDir: phase === PHASE_DEVELOPMENT_SERVER ? ".next-dev" : ".next",
  reactStrictMode: true,
  poweredByHeader: false,
  // dev-only badge: keep it off the sidebar's collapse button (bottom-left)
  devIndicators: { position: "bottom-right" },
  async rewrites() {
    return [
      // backend liveness lives at the root; every other backend route is under /api
      { source: "/api/health", destination: `${backendUrl}/health` },
      { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
    ];
  },
});

export default nextConfig;
