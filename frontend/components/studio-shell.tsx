"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  LayoutDashboard,
  FolderKanban,
  Users,
  Image as ImageIcon,
  Film,
  Settings,
  LogOut,
  Sparkles,
  HardDrive,
  Activity,
  Menu,
  Layers3,
  Clapperboard,
  FileText,
  PackageCheck,
  CircleHelp,
} from "lucide-react";
import { apiFetch, getStoredToken, loginRequest } from "@/lib/api";
import { useAuthStore } from "@/lib/store";
import type { User } from "@/lib/types";
import { cn } from "@/lib/utils";

const NAV_GROUPS = [
  { label: "WORKSPACE", items: [
    { href: "/", label: "Tổng quan", icon: LayoutDashboard },
    { href: "/projects", label: "Dự án", icon: FolderKanban },
    { href: "/projects", label: "Sản xuất hàng loạt", icon: Layers3 },
  ] },
  { label: "THƯ VIỆN", items: [
    { href: "/characters", label: "Nhân vật", icon: Users },
    { href: "/backgrounds", label: "Bối cảnh", icon: ImageIcon },
  ] },
  { label: "SẢN XUẤT", items: [
    { href: "/projects", label: "Kịch bản & Storyboard", icon: FileText },
    { href: "/exports", label: "Hàng đợi video", icon: Clapperboard },
    { href: "/exports", label: "Bản xuất", icon: PackageCheck },
  ] },
  { label: "HỆ THỐNG", items: [
    { href: "/settings", label: "Flow2API & Worker", icon: Settings },
  ] },
];

export function StudioShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, setAuth, logout } = useAuthStore();
  const [booting, setBooting] = useState(true);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function ensureSession() {
      if (pathname.startsWith("/login")) {
        setBooting(false);
        return;
      }
      const existing = getStoredToken();
      if (existing) {
        try {
          const me = await apiFetch<User>("/auth/me");
          if (!cancelled) {
            setAuth(existing, me);
            setBooting(false);
          }
          return;
        } catch {
          // fall through to auto dev sign-in
        }
      }
      // Seamless local studio auto-login with default bootstrap admin if unchanged
      try {
        const res = await loginRequest("admin@aivideostudio.app", "ChangeMe123!");
        if (!cancelled) {
          setAuth(res.access_token, res.user);
          setBooting(false);
        }
      } catch {
        if (!cancelled) {
          setBooting(false);
          router.replace("/login");
        }
      }
    }
    ensureSession();
    return () => {
      cancelled = true;
    };
  }, [pathname, router, setAuth]);

  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: () =>
      apiFetch<{
        status: string;
        database: string;
        redis: string;
        ffmpeg: string;
        disk: { free_gb: number; total_gb: number; healthy: boolean };
      }>("/health"),
    enabled: !pathname.startsWith("/login") && !booting,
    refetchInterval: 15_000,
  });

  if (pathname.startsWith("/login")) {
    return <>{children}</>;
  }

  if (booting) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50">
        <div className="flex items-center gap-3 text-slate-500">
          <div className="w-5 h-5 rounded-full border-2 border-green-500 border-t-transparent animate-spin" />
          <span className="text-sm font-medium">Đang khởi tạo AI Video Studio...</span>
        </div>
      </div>
    );
  }

  const disk = healthQuery.data?.disk;

  return (
    <div className="min-h-screen flex bg-slate-50 text-slate-900">
      <button className="mobile-menu" aria-label="Mở điều hướng" onClick={() => setMobileNavOpen(!mobileNavOpen)}><Menu size={20} /></button>
      {/* Sidebar */}
      <aside className={cn("app-sidebar", mobileNavOpen && "mobile-open")}>
        <div className="brand-lockup">
          <div className="brand-mark"><Sparkles className="w-4 h-4 text-white" />
          </div>
          <div>
            <div className="text-sm font-semibold tracking-tight text-slate-900">AI Video Studio</div>
            <div className="text-xs text-slate-500">AI Production Workspace</div>
          </div>
        </div>

        <nav className="flex-1 sidebar-nav">
          {NAV_GROUPS.map((group) => <section key={group.label} className="nav-group">
            <p className="nav-label">{group.label}</p>
            {group.items.map((item, index) => {
            const Icon = item.icon;
            const active = item.href === "/" ? pathname === "/" : pathname === item.href || pathname.startsWith(`${item.href}/`);
            const secondaryProjectLink = item.label === "Sản xuất hàng loạt" || item.label === "Kịch bản & Storyboard";
            return (
              <Link
                key={`${item.label}-${index}`}
                href={item.href}
                onClick={() => setMobileNavOpen(false)}
                className={cn(
                  "nav-item",
                  active
                    ? "nav-item-active"
                    : "text-slate-600 hover:text-slate-900 hover:bg-slate-50",
                  secondaryProjectLink && "nav-item-muted"
                )}
              >
                <Icon className="w-4 h-4 shrink-0" />
                {item.label}
              </Link>
            );
          })}
          </section>)}
        </nav>

        {/* System Health Footer */}
        <div className="health-summary">
          <div className="flex items-center justify-between">
            <span className="text-slate-500 flex items-center gap-1.5">
              <Activity className="w-3.5 h-3.5 text-green-600" />
              Engine
            </span>
              <span className="health-status">
              <span className={cn("health-dot", healthQuery.data?.ffmpeg === "ok" && "health-dot-ok")} />
              {healthQuery.data?.ffmpeg === "ok" ? "Worker hoạt động" : "Đang kiểm tra"}
            </span>
          </div>
          {disk && (
            <div className="flex items-center justify-between text-slate-500">
              <span className="flex items-center gap-1.5">
                <HardDrive className="w-3.5 h-3.5 text-slate-400" />
                Ổ cứng trống
              </span>
              <span className={cn("font-mono", disk.healthy ? "text-slate-700" : "text-rose-600")}>
                {disk.free_gb} GB
              </span>
            </div>
          )}
        </div>

        <div className="p-3 border-t border-slate-200 flex items-center justify-between">
          <div className="min-w-0">
            <p className="text-xs font-medium text-slate-800 truncate">{user?.full_name || "Studio Admin"}</p>
            <p className="text-[11px] text-slate-400 truncate">{user?.email || "admin@aivideostudio.app"}</p>
          </div>
          <button
            onClick={() => {
              logout();
              router.push("/login");
            }}
            title="Đăng xuất"
            className="p-2 rounded-lg text-slate-400 hover:text-rose-500 hover:bg-slate-100 transition"
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 min-w-0 flex flex-col overflow-x-hidden">{children}</main>
    </div>
  );
}
