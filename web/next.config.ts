import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 静态导出：产物 web/out 由 Python server 托管（运行时零 Node）
  output: "export",
  // 开发模式：/api 代理到本地后端（生产同源静态+API，无需代理）
  ...(process.env.NODE_ENV === "development"
    ? {
        async rewrites() {
          return [
            {
              source: "/api/:path*",
              destination: "http://127.0.0.1:8300/api/:path*",
            },
          ];
        },
      }
    : {}),
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
