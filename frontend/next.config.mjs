/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Прокси API на локальный бэкенд (замкнутый контур, без облака)
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
