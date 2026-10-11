import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "AI IND Money",
    short_name: "AI IND",
    description: "Adaptive-AI trading app for Indian equities",
    id: "/",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#f3f5f9",
    theme_color: "#5367ff",
    categories: ["finance", "business"],
    shortcuts: [
      { name: "Market", url: "/market", icons: [{ src: "/icon-192.png", sizes: "192x192" }] },
      { name: "Portfolio", url: "/portfolio", icons: [{ src: "/icon-192.png", sizes: "192x192" }] },
      { name: "AI insights", url: "/ai", icons: [{ src: "/icon-192.png", sizes: "192x192" }] },
      { name: "Price alerts", url: "/alerts", icons: [{ src: "/icon-192.png", sizes: "192x192" }] },
    ],
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
