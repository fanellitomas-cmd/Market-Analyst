import type { NextConfig } from "next";

// El navegador sólo habla con Next.js: /api/* se reenvía al backend FastAPI. Así la cookie de
// sesión queda en el mismo origen (httpOnly, SameSite=Lax) y no hace falta CORS.
const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
  poweredByHeader: false,
  // Servidor autocontenido para la imagen de Docker.
  output: "standalone",
};

export default nextConfig;
