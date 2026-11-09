import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Minimal, self-contained runtime image for docker/web.Dockerfile — see
  // nextjs.org/docs/app/api-reference/config/next-config-js/output.
  output: "standalone",
};

export default nextConfig;
