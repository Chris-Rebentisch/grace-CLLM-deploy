"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/chat", label: "Chat" },
  { href: "/graph", label: "Graph" },
  { href: "/inspector", label: "Inspector" },
  { href: "/review", label: "Review" },
  // D235 — Chunk 30 frontend route additions.
  { href: "/claims", label: "Claims" },
  { href: "/sources", label: "Sources" },
  { href: "/onboarding", label: "Onboarding" },
  { href: "/settings", label: "Settings" },
  { href: "/change-directives", label: "Directives" },
  // Chunk 42 — Permission Matrix surface (D331/D333/D337).
  { href: "/permissions", label: "Permissions" },
  // Chunk 43 — Sensitivity Gate Compliance Surface (D343/D344).
  { href: "/sensitivity", label: "Sensitivity" },
] as const;

export function AppNav() {
  const pathname = usePathname();
  return (
    <nav
      data-testid="app-nav"
      className="flex items-center gap-1 px-4 py-1 border-b border-border bg-white"
      aria-label="Primary"
    >
      {TABS.map((tab) => {
        const active = pathname === tab.href || pathname?.startsWith(`${tab.href}/`);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            data-testid={`nav-tab-${tab.label.toLowerCase()}`}
            aria-current={active ? "page" : undefined}
            className={cn(
              "rounded-md px-3 py-1 text-xs font-medium transition-colors",
              active
                ? "bg-slate-800 text-white"
                : "text-slate-700 hover:bg-slate-100",
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
