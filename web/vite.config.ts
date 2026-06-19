import { fileURLToPath, URL } from "node:url"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { VitePWA } from "vite-plugin-pwa"

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    // PWA:autoUpdate 让新构建的 SW 自动接管(不卡旧版);injectRegister auto 自动注入注册,
    // 无需手改 main.tsx。workbox 只预缓存构建产物,/api 与 /hook 走网络(实时数据 + 设备 SSRF 地址,绝不能命中缓存)。
    VitePWA({
      registerType: "autoUpdate",
      injectRegister: "auto",
      includeAssets: ["favicon.svg", "icons/*.png"],
      manifest: {
        name: "sms-hub",
        short_name: "sms-hub",
        description: "瘦终端 + Hub 短信网关",
        lang: "zh-CN",
        theme_color: "#0a0a0a",
        background_color: "#ffffff",
        display: "standalone",
        orientation: "portrait",
        scope: "/",
        start_url: "/",
        icons: [
          { src: "icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
          { src: "icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
          { src: "icons/maskable-192.png", sizes: "192x192", type: "image/png", purpose: "maskable" },
          { src: "icons/maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        navigateFallback: "index.html",
        navigateFallbackDenylist: [/^\/api\//, /^\/hook\//],
        globPatterns: ["**/*.{js,css,html,svg,png,ico,woff2}"],
      },
    }),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
})
