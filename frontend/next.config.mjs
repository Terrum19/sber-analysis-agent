/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Прокси API на бэкенд (замкнутый контур, без облака). rewrites() считается
  // при `next build`, поэтому NEXT_PUBLIC_API_URL нужен на этапе сборки образа
  // (build-arg в Dockerfile); без него — локальный бэкенд для `npm run dev`.
  webpack: (config) => {
    config.externals = [...(config.externals || []), { canvas: "canvas" }];
    config.resolve.fallback = {
      ...config.resolve.fallback,
      canvas: false,
    };
    return config;
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
