import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/components/providers";
import { StudioShell } from "@/components/studio-shell";

export const metadata: Metadata = {
  title: "AI Video Studio — Batch Continuity Video Production",
  description: "Automated AI Video Studio powered by Flow2API, Continuity Engine, and FFmpeg",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi">
      <body>
        <Providers>
          <StudioShell>{children}</StudioShell>
        </Providers>
      </body>
    </html>
  );
}

