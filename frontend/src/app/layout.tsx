import type { Metadata } from "next";
import { Geist } from "next/font/google";
import { cookies } from "next/headers";
import type { ReactNode } from "react";

import "./globals.css";
import { AppHeader } from "@/components/app-header";
import { AppSidebar } from "@/components/app-sidebar";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { THEME_BOOT_SCRIPT } from "@/lib/theme";
import { cn } from "@/lib/utils";

import { Providers } from "./providers";

const geist = Geist({ subsets: ["latin"], variable: "--font-sans" });

export const metadata: Metadata = {
  title: "FinTrack v2",
  description: "Learning-first NSE research and paper-trading platform",
};

export default async function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  // the sidebar saves its open/collapsed state in this cookie; reading it here keeps a collapsed
  // menu collapsed after a reload (no flash of the open menu)
  const sidebarOpen = (await cookies()).get("sidebar_state")?.value !== "false";
  return (
    // the boot script sets the theme class before paint; React must not "fix" it back
    <html lang="en" className={cn("dark font-sans", geist.variable)} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="antialiased">
        <Providers>
          <TooltipProvider>
            <SidebarProvider defaultOpen={sidebarOpen}>
              <AppSidebar />
              <SidebarInset className="min-w-0">
                <AppHeader />
                {children}
              </SidebarInset>
            </SidebarProvider>
          </TooltipProvider>
        </Providers>
      </body>
    </html>
  );
}
