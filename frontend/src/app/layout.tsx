import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "FinTrack v2",
  description: "Learning-first NSE research and paper-trading platform",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="bg-neutral-950 text-neutral-100 antialiased">
        <nav className="border-b border-neutral-800">
          <div className="mx-auto flex max-w-5xl gap-5 px-4 py-2 text-sm text-neutral-300">
            <Link href="/" className="hover:text-white">
              Home
            </Link>
            <Link href="/charts" className="hover:text-white">
              Charts
            </Link>
            <Link href="/admin" className="hover:text-white">
              Admin
            </Link>
          </div>
        </nav>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
