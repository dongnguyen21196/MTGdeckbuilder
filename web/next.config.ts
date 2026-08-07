import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Trên Vercel, rewrite ở vercel.json chặn /api/* trước khi request tới được
  // service này, nên rewrite dưới đây chỉ có tác dụng khi chạy local — lúc đó
  // Next ở :3000 còn FastAPI ở :8000.
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
