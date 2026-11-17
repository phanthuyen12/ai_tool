"use client";

import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient, useQueries } from "@tanstack/react-query";
import { Film, Download, XCircle, ChevronDown, ChevronUp, FileText } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useToastStore } from "@/lib/store";
import type { GenerationJob, Project, RenderItem, Scene } from "@/lib/types";
import { formatBytes, cn } from "@/lib/utils";

export default function ExportsAndJobsPage() {
  const qc = useQueryClient();
  const pushToast = useToastStore((s) => s.push);
  const [expandedJobId, setExpandedJobId] = useState<string | null>(null);
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: () => apiFetch<Project[]>("/projects") });
  const scenesQueries = useQueries({
    queries: (projectsQuery.data || []).map((project) => ({
      queryKey: ["scenes", project.id],
      queryFn: () => apiFetch<Scene[]>(`/scenes?project_id=${project.id}`),
      refetchInterval: 6_000,
    })),
  });
  const sceneClips = scenesQueries.flatMap((query, index) =>
    (query.data || []).filter((scene) => !!scene.video_url).map((scene) => ({ scene, project: projectsQuery.data?.[index] }))
  );

  const rendersQuery = useQuery({
    queryKey: ["renders"],
    queryFn: () => apiFetch<RenderItem[]>("/renders"),
    refetchInterval: 5_000,
  });

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: () => apiFetch<GenerationJob[]>("/jobs"),
    refetchInterval: 4_000,
  });

  const cancelJobMutation = useMutation({
    mutationFn: (jobId: string) => apiFetch<GenerationJob>(`/jobs/${jobId}/cancel`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      pushToast({ title: "Đã hủy tác vụ", variant: "info" });
    },
  });

  return (
    <div className="page-content p-6 lg:p-8 w-full mx-auto space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 flex items-center gap-2.5">
          <Film className="w-6 h-6 text-green-600" />
          Kho Video Hoàn chỉnh (FFmpeg Exports) & Lịch sử Hàng đợi
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          Xem trực tiếp các bản xuất MP4 H.264/AAC, tải phụ đề .SRT và kiểm tra nhật ký từng lần gọi Flow2API.
        </p>
      </div>

      <section className="space-y-3">
        <div className="flex items-end justify-between gap-3"><div><h2 className="text-base font-semibold text-slate-900">Clip theo từng cảnh</h2><p className="text-sm text-slate-500 mt-1">Video do Omni tạo sẽ xuất hiện tại đây ngay khi scene hoàn tất.</p></div><span className="text-xs text-slate-500">{sceneClips.length} clip</span></div>
        {sceneClips.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-300 bg-white p-7 text-center"><Film className="mx-auto mb-2 text-slate-400" size={22} /><p className="text-sm font-medium text-slate-700">Chưa có clip cảnh nào</p><p className="mt-1 text-xs text-slate-500">Sau khi job scene_video hoàn tất, clip sẽ được liệt kê ở đây.</p></div>
        ) : <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {sceneClips.map(({ scene, project }) => <article key={scene.id} className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <div className="aspect-video bg-slate-950"><video src={scene.video_url!} controls preload="metadata" playsInline className="h-full w-full object-contain" aria-label={`Video ${scene.title}`} /></div>
            <div className="p-3"><p className="truncate text-sm font-semibold text-slate-900">Scene {scene.scene_index} · {scene.title}</p><p className="mt-1 truncate text-xs text-slate-500">{project?.name || "Dự án"} · {scene.planned_duration_sec}s</p><a href={`/projects/${scene.project_id}`} className="mt-3 inline-flex min-h-9 items-center rounded-lg border border-slate-200 px-3 text-xs font-medium text-slate-700 hover:bg-slate-50">Mở Studio</a></div>
          </article>)}
        </div>}
      </section>

      {/* Rendered Videos Gallery */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-900">Danh sách Video đã ghép (Persistent Storage)</h2>
        {(rendersQuery.data || []).length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-200 p-10 text-center text-sm text-slate-500">
            Chưa có video nào được render.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {(rendersQuery.data || []).map((r) => (
              <div
                key={r.id}
                className="rounded-2xl border border-slate-200 bg-white overflow-hidden flex flex-col justify-between shadow-sm"
              >
                <div className="bg-black aspect-[9/16] max-h-80 flex items-center justify-center">
                  {r.mp4_url ? (
                    <video
                      src={r.mp4_url}
                      controls
                      preload="metadata"
                      className="w-full h-full object-contain"
                    />
                  ) : (
                    <span className="text-xs text-slate-400 uppercase font-mono">{r.status}</span>
                  )}
                </div>
                <div className="p-4 space-y-3">
                  <div>
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="text-sm font-bold text-slate-900 truncate">
                        {r.project_name || "Dự án"} • v{r.version}
                      </h3>
                      <span
                        className={cn(
                          "px-2 py-0.5 rounded-full text-[10px] font-medium border",
                          r.status === "completed"
                            ? "bg-green-50 text-green-700 border-green-200"
                            : r.status === "failed"
                            ? "bg-rose-50 text-rose-700 border-rose-200"
                            : "bg-sky-50 text-sky-700 border-sky-200"
                        )}
                      >
                        {r.status}
                      </span>
                    </div>
                    <p className="text-xs text-slate-500 mt-1">
                      {r.resolution} • {r.duration_seconds.toFixed(1)}s • {formatBytes(r.file_size_bytes)}
                    </p>
                    {r.error_message && <p className="text-xs text-rose-600 mt-1">{r.error_message}</p>}
                  </div>

                  <div className="flex items-center gap-2">
                    {r.mp4_url && (
                      <a
                        href={`${r.mp4_url}${r.mp4_url.includes("?") ? "&" : "?"}download=1`}
                        className="flex-1 inline-flex items-center justify-center gap-1.5 rounded-xl bg-green-600 hover:bg-green-700 py-2 text-xs font-semibold text-white shadow-sm shadow-green-600/20 transition"
                      >
                        <Download className="w-3.5 h-3.5" /> Tải MP4
                      </a>
                    )}
                    {r.srt_url && (
                      <a
                        href={`${r.srt_url}${r.srt_url.includes("?") ? "&" : "?"}download=1`}
                        className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-slate-200 hover:bg-slate-50 px-3 py-2 text-xs font-medium text-slate-700 transition"
                      >
                        <FileText className="w-3.5 h-3.5" /> .SRT
                      </a>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Generation Jobs & Attempt History */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-900">Nhật ký Tác vụ & Chi tiết Retry (Generation Jobs)</h2>
        <div className="rounded-2xl border border-slate-200 bg-white divide-y divide-slate-100 shadow-sm">
          {(jobsQuery.data || []).length === 0 ? (
            <div className="p-8 text-center text-xs text-slate-400">Chưa có tác vụ nào trong lịch sử.</div>
          ) : (
            (jobsQuery.data || []).map((job) => {
              const expanded = expandedJobId === job.id;
              return (
                <div key={job.id} className="p-4 space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="space-y-0.5">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono font-semibold text-green-700">{job.job_type}</span>
                        <span className="text-xs text-slate-500">({job.provider_model})</span>
                        <span
                          className={cn(
                            "px-2 py-0.5 rounded-full text-[10px] font-medium border",
                            job.status === "completed"
                              ? "bg-green-50 text-green-700 border-green-200"
                              : job.status === "failed"
                              ? "bg-rose-50 text-rose-700 border-rose-200"
                              : "bg-sky-50 text-sky-700 border-sky-200"
                          )}
                        >
                          {job.status} ({job.progress_percent}%)
                        </span>
                      </div>
                      <p className="text-xs text-slate-500">
                        {job.status_message} • Thử {job.attempt_count}/{job.max_attempts}
                      </p>
                      {job.error_message && (
                        <p className="text-xs text-rose-600">
                          [{job.error_code || "ERROR"}] {job.error_message}
                        </p>
                      )}
                    </div>

                    <div className="flex items-center gap-2">
                      {(job.status === "queued" || job.status === "running") && (
                        <button
                          onClick={() => cancelJobMutation.mutate(job.id)}
                          className="inline-flex items-center gap-1 rounded-lg border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-xs text-rose-600 hover:bg-rose-100"
                        >
                          <XCircle className="w-3.5 h-3.5" /> Dừng job
                        </button>
                      )}
                      <button
                        onClick={() => setExpandedJobId(expanded ? null : job.id)}
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
                      >
                        {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                        Chi tiết ({job.attempts.length})
                      </button>
                    </div>
                  </div>

                  {expanded && (
                    <div className="rounded-xl bg-slate-50 border border-slate-200 p-3 space-y-2 text-xs">
                      {job.attempts.length === 0 ? (
                        <p className="text-slate-400">Chưa có log request ngoại vi.</p>
                      ) : (
                        job.attempts.map((a) => (
                          <div key={a.id} className="border-b last:border-b-0 border-slate-200 pb-2 last:pb-0 space-y-1">
                            <div className="flex items-center justify-between text-slate-600 font-mono">
                              <span>
                                Attempt #{a.attempt_number} • {a.model_used} • {a.status}
                              </span>
                              <span>{a.duration_ms} ms</span>
                            </div>
                            {a.upstream_error_code && (
                              <p className="text-rose-600">
                                Upstream: {a.upstream_error_code} — {a.error_message}
                              </p>
                            )}
                            {a.prompt_sent && (
                              <p className="text-slate-500 line-clamp-3 font-mono text-[11px]">{a.prompt_sent}</p>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </section>
    </div>
  );
}
