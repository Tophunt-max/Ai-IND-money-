import type { Metadata, Viewport } from "next";

import AuthGate from "@/components/AuthGate";
import PwaRegister from "@/components/PwaRegister";
import Shell from "@/components/Shell";

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
  viewportFit: "cover",
  themeColor: "#05070d",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <PwaRegister />
        <AuthGate>
          <Shell>{children}</Shell>
        </AuthGate>
      </body>
    </html>
  );
}
