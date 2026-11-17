import type { NextConfig } from "next";

const internalApi = process.env.INTERNAL_API_URL || "http://127.0.0.1:8010";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${internalApi}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;

