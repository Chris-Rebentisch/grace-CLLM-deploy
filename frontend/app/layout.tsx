import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { PhaseBanner } from "@/components/session/PhaseBanner";
import { SessionHeader } from "@/components/session/SessionHeader";
import { ScopeIndicator } from "@/components/scope/ScopeIndicator";
import { AppNav } from "@/components/nav/AppNav";

export const metadata: Metadata = {
  title: "GrACE",
  description: "Graph as Compression Engine",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background text-foreground antialiased">
        <Providers>
          <div className="flex min-h-screen flex-col">
            <header className="flex items-center gap-4 border-b border-border px-4 py-3">
              <div className="font-semibold tracking-tight">GrACE</div>
              <SessionHeader />
              <div className="flex-1" />
              <ScopeIndicator />
            </header>
            <PhaseBanner />
            <AppNav />
            <main className="flex-1">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
