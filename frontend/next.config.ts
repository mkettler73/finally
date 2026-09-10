import path from "node:path";

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  reactStrictMode: true,
  // Pin the workspace root: a stray package-lock.json above the repo otherwise
  // makes Turbopack guess, and it warns on every build.
  turbopack: { root: path.resolve(import.meta.dirname) },
};

export default nextConfig;
