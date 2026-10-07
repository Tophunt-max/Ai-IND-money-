import type { Metadata, Viewport } from "next";

import AuthGate from "@/components/AuthGate";
import Nav from "@/components/Nav";
import PwaRegister from "@/components/PwaRegister";

import "./globals.css";

export const metadata: Metadata = {
  title: "AI IND Money",
  description: "AI algorithmic trading dashboard for Indian equities",
  applicationName: "AI IND Money",
  appleWebApp: { capable: true, title: "AI IND", statusBarStyle: "black-translucent" },
  icons: {
    icon: [{ url: "/icon-192.png", sizes: "192x192", type: "image/png" }],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180" }],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#030712",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-gray-950 text-gray-100 min-h-screen">
        <PwaRegister />
        <AuthGate>
          <Nav />
          <main className="max-w-6xl mx-auto px-4 md:px-6 py-6 pb-24 md:pb-10">{children}</main>
        </AuthGate>
      </body>
    </html>
  );
}
