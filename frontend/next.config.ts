import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  output: "standalone",
  reactCompiler: true,
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
