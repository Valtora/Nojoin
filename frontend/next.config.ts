import type { NextConfig } from "next";

// Note: Content-Security-Policy is emitted per-request by proxy.ts so
// it can include a fresh nonce on every response. All other security
// headers are static and applied here.
const securityHeaders = [
  {
    key: "Referrer-Policy",
    value: "strict-origin-when-cross-origin",
  },
  {
    key: "X-Content-Type-Options",
    value: "nosniff",
  },
  {
    key: "X-Frame-Options",
    value: "DENY",
  },
  {
    key: "Permissions-Policy",
    value: "camera=(), geolocation=(), payment=(), usb=()",
  },
];

const nextConfig: NextConfig = {
  /* config options here */
  output: "standalone",
  poweredByHeader: false,
  reactCompiler: true,
  // `next dev` writes AGENTS.md and CLAUDE.md into frontend/ on every start
  // when it detects an AI coding agent, to point that agent at the docs bundled
  // in node_modules. This repository does not carry agent rules files, so the
  // only thing that arrives is two untracked files in the working tree of
  // whoever happened to run the dev server.
  agentRules: false,
  turbopack: {
    root: __dirname,
  },
  async redirects() {
    return [
      {
        source: "/settings/companion",
        destination: "/settings/capture",
        permanent: false,
      },
      {
        source: "/settings/audio",
        destination: "/settings/capture",
        permanent: false,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
