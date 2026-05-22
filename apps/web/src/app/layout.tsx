import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/components/providers";
import { SiteHeader } from "@/components/site-header";
import { LimitBannerShell } from "@/components/billing/limit-banner-shell";

export const metadata: Metadata = {
  title: "CivicSignals",
  description: "Open-source public-sector sales intelligence.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen bg-background text-foreground antialiased">
        <Providers>
          <SiteHeader />
          {/* N4: soft-limit banner — shown when any dimension ≥ 80% or ≥ 100% */}
          <LimitBannerShell />
          {children}
        </Providers>
      </body>
    </html>
  );
}
