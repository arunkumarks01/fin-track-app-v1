"use client";

import {
  CandlestickChart,
  BookOpen,
  ChartNoAxesCombined,
  ChevronsLeft,
  ChevronsRight,
  Gauge,
  LayoutDashboard,
  LineChart,
  ShieldCheck,
  ListFilter,
  Workflow,
} from "lucide-react";
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
  useSidebar,
} from "@/components/ui/sidebar";

// Only pages that exist. New items appear when their phase lands — never placeholders.
const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/stocks", label: "Stocks", icon: LineChart },
  { href: "/charts", label: "Charts", icon: CandlestickChart },
  { href: "/options", label: "Options", icon: Workflow },
  { href: "/candidates", label: "Candidates", icon: ListFilter },
  { href: "/paper", label: "Paper trading", icon: BookOpen },
  { href: "/backtest", label: "Backtest learning", icon: ChartNoAxesCombined },
];
const SYSTEM = [{ href: "/admin", label: "Admin · Health", icon: ShieldCheck }];

function isActive(path: string, href: string): boolean {
  return href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`);
}

/** A visible collapse / expand control (also: the top-bar button, Ctrl/⌘+B, the sidebar edge). */
function CollapseButton() {
  const { state, toggleSidebar, isMobile } = useSidebar();
  if (isMobile) return null; // on phones the menu is a slide-out sheet with its own close
  const collapsed = state === "collapsed";
  const label = collapsed ? "Expand menu" : "Collapse menu";
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton onClick={toggleSidebar} tooltip={label} aria-label={label}>
          {collapsed ? <ChevronsRight aria-hidden /> : <ChevronsLeft aria-hidden />}
          <span>{label}</span>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  );
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
                  <span className="text-sm text-sidebar-foreground/60">v2 · research</span>
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
        <p className="px-2 text-sm text-sidebar-foreground/60 group-data-[collapsible=icon]:hidden">
          Phase 1 · data foundation. Paper only.
        </p>
        <CollapseButton />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
