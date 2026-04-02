/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  webpack: (config) => {
    // pdfjs-dist optionally requires the native "canvas" package (Node-only).
    // Aliasing it to false prevents webpack from trying to bundle it.
    config.resolve.alias.canvas = false;
    return config;
  },
};

export default nextConfig;
