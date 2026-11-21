"use client";

import { CandlestickChart, Gauge, LayoutDashboard, LineChart, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";

// Only pages that exist. New items appear when their phase lands — never placeholders.
const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/stocks", label: "Stocks", icon: LineChart },
  { href: "/charts", label: "Charts", icon: CandlestickChart },
];
const SYSTEM = [{ href: "/admin", label: "Admin · Health", icon: ShieldCheck }];

function isActive(path: string, href: string): boolean {
  return href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`);
}

export function AppSidebar() {
  const path = usePathname();
  const items = (list: typeof NAV) =>
    list.map((n) => (
      <SidebarMenuItem key={n.href}>
        <SidebarMenuButton asChild isActive={isActive(path, n.href)} tooltip={n.label}>
          <Link href={n.href}>
            <n.icon aria-hidden />
            <span>{n.label}</span>
          </Link>
        </SidebarMenuButton>
      </SidebarMenuItem>
    ));
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" asChild>
              <Link href="/">
                <span className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
                  <Gauge className="size-4" aria-hidden />
                </span>
                <span className="grid leading-tight">
                  <span className="font-semibold">FinTrack</span>
                  <span className="text-xs text-sidebar-foreground/60">v2 · research</span>
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Menu</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>{items(NAV)}</SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
        <SidebarGroup>
          <SidebarGroupLabel>System</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>{items(SYSTEM)}</SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <p className="px-2 text-xs text-sidebar-foreground/60 group-data-[collapsible=icon]:hidden">
          Phase 1 · data foundation. Paper only.
        </p>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
