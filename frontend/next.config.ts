import type { NextConfig } from "next";

import { requireHttpUrl } from "./src/lib/env";

// Fail closed: no backend URL, no build.
const backendUrl = requireHttpUrl("BACKEND_URL");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [
      // backend liveness lives at the root; every other backend route is under /api
      { source: "/api/health", destination: `${backendUrl}/health` },
      { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
    ];
  },
};

export default nextConfig;
