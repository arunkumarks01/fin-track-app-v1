import type { NextConfig } from "next";

import { requireHttpUrl } from "./src/lib/env";

// Fail closed: no backend URL, no build.
const backendUrl = requireHttpUrl("BACKEND_URL");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/:path*` }];
  },
};

export default nextConfig;
