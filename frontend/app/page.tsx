"use client";

import React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  FolderKanban,
  Video,
  CheckCircle2,
  AlertCircle,
  Clock,
  Plus,
  Users,
  Settings,
  Download,
  Play,
  ArrowUpRight,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import type { Project, RenderItem } from "@/lib/types";
import { formatBytes, formatTimecode, cn } from "@/lib/utils";

interface DashboardSummary {
  metrics: {
    total_projects: number;
    generating_videos: number;
    completed_videos: number;
    failed_jobs: number;
    total_rendered_duration_seconds: number;
  };
  disk: {
    total_gb: number;
    used_gb: number;
    free_gb: number;
    min_required_gb: number;
    healthy: boolean;
  };
  recent_batches: Array<{
    id: string;
    name: string;
    project_id: string;
    total_videos: number;
    completed_videos: number;
    failed_videos: number;
    status: string;
    created_at: string;
  }>;
  recent_jobs: Array<{
    id: string;
    project_id: string;
    scene_id?: string | null;
    job_type: string;
    provider_model: string;
    status: string;
    progress_percent: number;
    attempt_count: number;
    status_message?: string;
  }>;
}

export default function DashboardPage() {
  const summaryQuery = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => apiFetch<DashboardSummary>("/dashboard"),
    refetchInterval: 6_000,
  });

  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: () => apiFetch<Project[]>("/projects"),
  });

  const rendersQuery = useQuery({
    queryKey: ["renders"],
    queryFn: () => apiFetch<RenderItem[]>("/renders"),
  });

  const modelsQuery = useQuery({
    queryKey: ["models"],
    queryFn: () =>
      apiFetch<{ source: string; has_api_key: boolean; models: Array<{ id: string; type: string }> }>(
        "/providers/models"
      ),
  });

  const m = summaryQuery.data?.metrics;
  const completedRenders = (rendersQuery.data || []).filter((r) => r.status === "completed" && r.mp4_url);

  return (
    <div className="page-content p-6 lg:p-8 space-y-6 w-full mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <div className="text-xs text-slate-400 mb-1">Workspace <span className="px-1">/</span> Tổng quan</div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Tổng quan sản xuất</h1>
          <p className="text-sm text-slate-500 mt-1">
            Theo dõi dự án, tác vụ tạo video và các bản xuất của bạn.
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          <Link
            href="/characters"
            className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 px-3.5 py-2.5 text-sm font-medium text-slate-700 transition"
          >
            <Users className="w-4 h-4 text-green-600" />
            Thư viện Nhân vật
          </Link>
          <Link
            href="/projects"
            className="inline-flex items-center gap-2 rounded-lg bg-green-700 hover:bg-green-800 px-4 py-2.5 text-sm font-semibold text-white transition"
          >
            <Plus className="w-4 h-4" />
            Tạo Dự án Mới
          </Link>
        </div>
      </div>

      {/* Flow2API Key Banner if missing */}
      {modelsQuery.data && !modelsQuery.data.has_api_key && (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-0.5">
            <p className="text-sm font-semibold text-amber-800">
              Chưa gắn khóa FLOW2API_API_KEY (đang ở chế độ Local / Offline Ready)
            </p>
            <p className="text-xs text-amber-700/90">
              Bạn vẫn có thể tạo dự án, sinh kịch bản + bộ đôi Prompt 8 lớp, tải clip lên và ghép FFmpeg ngay. Để gọi Nano Banana 2.1 & Omni 1.2 tự động, hãy nhập API Key trong mục Cấu hình.
            </p>
          </div>
          <Link
            href="/settings"
            className="inline-flex items-center gap-1.5 rounded-xl bg-amber-500 hover:bg-amber-600 px-3.5 py-2 text-xs font-semibold text-white transition"
          >
            <Settings className="w-3.5 h-3.5" />
            Cấu hình Flow2API
          </Link>
        </div>
      )}

      {/* KPI Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <KpiCard
          label="Tổng số dự án"
          value={m?.total_projects ?? 0}
          icon={<FolderKanban className="w-4 h-4 text-green-600" />}
        />
        <KpiCard
          label="Đang xử lý video"
          value={m?.generating_videos ?? 0}
          icon={<Video className="w-4 h-4 text-sky-600" />}
        />
        <KpiCard
          label="Video đã hoàn tất"
          value={m?.completed_videos ?? 0}
          icon={<CheckCircle2 className="w-4 h-4 text-green-600" />}
        />
        <KpiCard
          label="Tác vụ lỗi"
          value={m?.failed_jobs ?? 0}
          icon={<AlertCircle className="w-4 h-4 text-rose-500" />}
        />
        <KpiCard
          label="Tổng thời lượng xuất"
          value={formatTimecode(m?.total_rendered_duration_seconds ?? 0)}
          icon={<Clock className="w-4 h-4 text-emerald-600" />}
        />
      </div>

      {/* Main 2-column grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Recent Projects */}
        <div className="lg:col-span-2 rounded-xl border border-slate-200 bg-white p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-900">Dự án gần đây</h2>
            <Link href="/projects" className="text-xs text-green-600 hover:text-green-700 inline-flex items-center gap-1">
              Xem tất cả <ArrowUpRight className="w-3.5 h-3.5" />
            </Link>
          </div>

          {(projectsQuery.data || []).length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-200 p-8 text-center space-y-3">
              <p className="text-sm text-slate-500">Chưa có dự án nào. Hãy tạo dự án đầu tiên để bắt đầu dựng kịch bản & phân cảnh.</p>
              <Link
                href="/projects"
                className="inline-flex items-center gap-1.5 rounded-xl bg-green-600 hover:bg-green-700 px-3.5 py-2 text-xs font-semibold text-white"
              >
                <Plus className="w-4 h-4" /> Tạo dự án ngay
              </Link>
            </div>
          ) : (
            <div className="divide-y divide-slate-100">
              {(projectsQuery.data || []).slice(0, 6).map((p) => (
                <Link
                  key={p.id}
                  href={`/projects/${p.id}`}
                  className="py-3.5 flex items-center justify-between gap-4 hover:bg-slate-50 -mx-2 px-2 rounded-xl transition"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-slate-900 truncate">{p.name}</span>
                      <span className="px-2 py-0.5 rounded-md text-[10px] font-mono uppercase bg-slate-100 text-slate-600">
                        {p.aspect_ratio}
                      </span>
                      <StatusBadge status={p.status} />
                    </div>
                    <p className="text-xs text-slate-500 truncate mt-0.5">
                      {p.topic} • {p.target_duration_seconds}s • {p.scene_count} cảnh ({p.ready_videos_count}/{p.scene_count} clip, {p.completed_renders_count} bản xuất)
                    </p>
                  </div>
                  <span className="text-xs font-medium text-green-600 shrink-0">Mở Studio →</span>
                </Link>
              ))}
            </div>
          )}
        </div>

        {/* Latest Rendered Videos Player */}
        <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
              <Play className="w-4 h-4 text-green-600" />
              Video hoàn chỉnh mới nhất
            </h2>
            <Link href="/exports" className="text-xs text-green-600 hover:text-green-700">
              Kho xuất file
            </Link>
          </div>

          {completedRenders.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-200 p-6 text-center text-xs text-slate-500">
              Chưa có bản render MP4 nào hoàn tất.
            </div>
          ) : (
            <div className="space-y-3">
              {completedRenders.slice(0, 2).map((r) => (
                <div key={r.id} className="rounded-xl border border-slate-200 bg-slate-50 p-3 space-y-2.5">
                  <video
                    src={r.mp4_url || undefined}
                    controls
                    preload="metadata"
                    className="w-full max-h-64 rounded-lg bg-black object-contain"
                  />
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <div className="min-w-0">
                      <p className="font-medium text-slate-700 truncate">
                        {r.project_name || "Dự án"} (v{r.version})
                      </p>
                      <p className="text-slate-400">
                        {r.resolution} • {r.duration_seconds.toFixed(1)}s • {formatBytes(r.file_size_bytes)}
                      </p>
                    </div>
                    {r.mp4_url && (
                      <a
                        href={`${r.mp4_url}${r.mp4_url.includes("?") ? "&" : "?"}download=1`}
                        className="p-2 rounded-lg bg-white border border-slate-200 hover:bg-slate-100 text-slate-700"
                        title="Tải MP4"
                      >
                        <Download className="w-3.5 h-3.5" />
                      </a>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Live Job Feed */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900">Tiến trình hàng đợi gần nhất (Queue & Worker)</h2>
          <Link href="/exports" className="text-xs text-green-600 hover:text-green-700">
            Chi tiết lịch sử retry →
          </Link>
        </div>
        {(summaryQuery.data?.recent_jobs || []).length === 0 ? (
          <p className="text-sm text-slate-500">Chưa có generation job nào được gửi. Tạo dự án và bắt đầu sản xuất để theo dõi tiến trình tại đây.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs min-w-[760px]">
              <thead>
                <tr className="text-slate-400 border-b border-slate-200">
                  <th className="py-2 font-medium">Job / Scene</th>
                  <th className="py-2 font-medium">Dự án</th>
                  <th className="py-2 font-medium">Loại</th>
                  <th className="py-2 font-medium">Model / Engine</th>
                  <th className="py-2 font-medium">Trạng thái</th>
                  <th className="py-2 font-medium">Tiến độ</th>
                  <th className="py-2 font-medium">Lần thử</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {(summaryQuery.data?.recent_jobs || []).map((j) => (
                  <tr key={j.id} className="text-slate-600">
                    <td className="py-2.5 font-mono">{j.scene_id ? `Scene ${j.scene_id.slice(0, 8)}` : j.id.slice(0, 8)}</td>
                    <td className="py-2.5 font-mono text-slate-500">{j.project_id.slice(0, 8)}</td>
                    <td className="py-2.5 font-mono">{j.job_type}</td>
                    <td className="py-2.5 text-slate-500">{j.provider_model}</td>
                    <td className="py-2.5">
                      <StatusBadge status={j.status} />
                    </td>
                    <td className="py-2.5 w-44">
                      {j.status === "completed" ? <span className="text-emerald-700">Hoàn tất</span> : j.status === "running" && (j.progress_percent ?? 0) > 0 ? <div className="w-36 h-1.5 rounded-full bg-slate-100 overflow-hidden">
                        <div
                          className="h-full transition-all bg-sky-500"
                          style={{ width: `${Math.min(100, Math.max(0, j.progress_percent))}%` }}
                        />
                      </div> : <span className="text-slate-400">{j.status === "running" ? "Đang xử lý" : "—"}</span>}
                    </td>
                    <td className="py-2.5 font-mono text-slate-400">#{j.attempt_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function KpiCard({ label, value, icon }: { label: string; value: string | number; icon: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 flex flex-col justify-between gap-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-slate-500">{label}</span>
        <div>{icon}</div>
      </div>
      <div className="text-[28px] leading-8 font-semibold text-slate-900 tracking-tight">{value}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles =
    status === "completed" || status === "video_ready" || status === "approved"
      ? "bg-green-50 text-green-700 border-green-200"
      : status === "failed"
      ? "bg-rose-50 text-rose-700 border-rose-200"
      : status === "running" || status.includes("generating")
      ? "bg-sky-50 text-sky-700 border-sky-200"
      : "bg-slate-100 text-slate-600 border-slate-200";
  return (
    <span className={cn("px-2 py-0.5 rounded-full text-[10px] font-medium border", styles)}>
      {status}
    </span>
  );
}
