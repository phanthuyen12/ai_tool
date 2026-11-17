"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  FileText,
  ImagePlus,
  Video,
  CheckCircle2,
  Film,
  Download,
  Trash2,
  Lock,
  X,
  Square,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useToastStore } from "@/lib/store";
import type { Project, Scene, RenderItem, GenerationJob } from "@/lib/types";
import { cn, formatBytes } from "@/lib/utils";

export default function ProjectStudioPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const router = useRouter();
  const qc = useQueryClient();
  const pushToast = useToastStore((s) => s.push);

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<Project>(`/projects/${projectId}`),
  });

  const scenesQuery = useQuery({
    queryKey: ["scenes", projectId],
    queryFn: () => apiFetch<Scene[]>(`/scenes?project_id=${projectId}`),
    enabled: !!projectId,
    refetchInterval: () => {
      const jobs = qc.getQueryData<GenerationJob[]>(["jobs", projectId]) || [];
      return jobs.some((job) => job.status === "queued" || job.status === "running") ? 12_000 : false;
    },
  });

  const rendersQuery = useQuery({
    queryKey: ["renders", projectId],
    queryFn: () => apiFetch<RenderItem[]>(`/renders?project_id=${projectId}`),
    enabled: !!projectId,
    refetchInterval: () => {
      const jobs = qc.getQueryData<GenerationJob[]>(["jobs", projectId]) || [];
      return jobs.some((job) => job.status === "queued" || job.status === "running") ? 12_000 : false;
    },
  });

  const jobsQuery = useQuery({
    queryKey: ["jobs", projectId],
    queryFn: () => apiFetch<GenerationJob[]>(`/jobs?project_id=${projectId}`),
    enabled: !!projectId,
    refetchInterval: (query) => {
      const jobs = query.state.data as GenerationJob[] | undefined;
      return !jobs || jobs.some((job) => job.status === "queued" || job.status === "running") ? 4_000 : false;
    },
  });
  const autoProductionJob = (jobsQuery.data || []).find((job) => job.job_type === "project_auto_production" && ["queued", "running"].includes(job.status));
  const failedJobs = (jobsQuery.data || []).filter((job) => job.status === "failed");

  const autoProductionMutation = useMutation({
    mutationFn: () => apiFetch<{ id: string; status: string }>(`/projects/${projectId}/auto-produce`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs", projectId] });
      pushToast({ title: "Đã bắt đầu tự động sản xuất", description: "Ảnh từng cảnh → video → ghép MP4", variant: "success" });
    },
    onError: (err: unknown) => pushToast({ title: "Không thể chạy tự động", description: err instanceof Error ? err.message : "Sinh kịch bản trước rồi thử lại", variant: "error" }),
  });

  const cancelAutoProductionMutation = useMutation({
    mutationFn: (jobId: string) => apiFetch<GenerationJob>(`/jobs/${jobId}/cancel`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs", projectId] }),
    onError: (err: unknown) => pushToast({ title: "Không thể dừng pipeline", description: err instanceof Error ? err.message : "Lỗi không xác định", variant: "error" }),
  });

  const [forceOverwriteLocked, setForceOverwriteLocked] = useState(false);

  const generateScriptMutation = useMutation({
    mutationFn: () =>
      apiFetch("/scripts/generate", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId, force_overwrite_locked: forceOverwriteLocked }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scenes", projectId] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      pushToast({ title: "Đã sinh kịch bản & phân cảnh", variant: "success" });
    },
    onError: (err: unknown) => {
      pushToast({
        title: "Không thể sinh kịch bản",
        description: err instanceof Error ? err.message : "Lỗi không xác định",
        variant: "error",
      });
    },
  });

  const genImageMutation = useMutation({
    mutationFn: (sceneId: string) => apiFetch(`/scenes/${sceneId}/generate-image`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scenes", projectId] });
      pushToast({ title: "Đã đưa vào hàng đợi sinh ảnh", variant: "info" });
    },
    onError: (err: unknown) => {
      pushToast({
        title: "Không thể sinh ảnh",
        description: err instanceof Error ? err.message : "Lỗi không xác định",
        variant: "error",
      });
    },
  });

  const approveMutation = useMutation({
    mutationFn: (sceneId: string) => apiFetch(`/scenes/${sceneId}/approve`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scenes", projectId] });
      pushToast({ title: "Đã duyệt Master Image", variant: "success" });
    },
    onError: (err: unknown) => {
      pushToast({
        title: "Không thể duyệt",
        description: err instanceof Error ? err.message : "Lỗi không xác định",
        variant: "error",
      });
    },
  });

  const genVideoMutation = useMutation({
    mutationFn: (sceneId: string) => apiFetch(`/scenes/${sceneId}/generate-video`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scenes", projectId] });
      pushToast({ title: "Đã đưa vào hàng đợi sinh video", variant: "info" });
    },
    onError: (err: unknown) => {
      pushToast({
        title: "Không thể sinh video",
        description: err instanceof Error ? err.message : "Lỗi không xác định",
        variant: "error",
      });
    },
  });

  const [burnSubtitles, setBurnSubtitles] = useState(false);
  const [colorGrade, setColorGrade] = useState(true);

  const renderMutation = useMutation({
    mutationFn: () =>
      apiFetch("/renders", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          render_config_override: { burn_subtitles: burnSubtitles, color_grade: colorGrade },
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["renders", projectId] });
      pushToast({ title: "Đã đưa vào hàng đợi ghép video FFmpeg", variant: "info" });
    },
    onError: (err: unknown) => {
      pushToast({
        title: "Không thể ghép video",
        description: err instanceof Error ? err.message : "Hãy đảm bảo mọi cảnh đã có video",
        variant: "error",
      });
    },
  });

  const deleteProjectMutation = useMutation({
    mutationFn: () => apiFetch<void>(`/projects/${projectId}`, { method: "DELETE" }),
    onSuccess: () => {
      pushToast({ title: "Đã xóa dự án", variant: "info" });
      router.push("/projects");
    },
  });

  const project = projectQuery.data;
  const scenes = scenesQuery.data || [];
  const expectedAspect = project?.aspect_ratio === "16:9" ? 16 / 9 : project?.aspect_ratio === "1:1" ? 1 : 9 / 16;
  const aspectMismatchScenes = scenes.filter((scene) => {
    const clip = scene.assets.find((asset) => asset.asset_role === "scene_video" && asset.is_active);
    if (!clip?.width || !clip.height || project?.aspect_ratio === "1:1") return false;
    return Math.abs(Math.log((clip.width / clip.height) / expectedAspect)) > 0.16;
  });
  const allVideosReady = scenes.length > 0 && scenes.every((s) => !!s.video_url);
  const masterScene = scenes.find((s) => s.is_master_scene) || scenes[0];
  const masterReady =
    !!masterScene?.image_url && (masterScene.approval_status === "approved" || !!project?.auto_approve_master);

  if (projectQuery.isLoading) {
    return <div className="p-8 text-sm text-slate-500">Đang tải dự án...</div>;
  }
  if (!project) {
    return <div className="p-8 text-sm text-slate-500">Không tìm thấy dự án.</div>;
  }

  return (
    <div className="p-6 lg:p-8 max-w-7xl w-full mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <Link href="/projects" className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-600">
            <ArrowLeft className="w-3.5 h-3.5" /> Tất cả dự án
          </Link>
          <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5">
            {project.name}
            <StatusBadge status={project.status} />
          </h1>
          <p className="text-sm text-slate-500">
            {project.topic} • {project.target_duration_seconds}s • {project.aspect_ratio} • {project.language} • {project.script_mode === "literal" ? "Theo kịch bản nhập vào" : "Thuyết trình (AI viết)"} • {project.voice_mode === "ai_generated" ? "AI voice / lip-sync" : project.voice_mode === "silent" ? "Không lời thoại" : "External TTS"} • {project.image_model} + {project.video_model}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {autoProductionJob ? <button onClick={() => cancelAutoProductionMutation.mutate(autoProductionJob.id)} disabled={cancelAutoProductionMutation.isPending} className="inline-flex min-h-10 items-center gap-2 rounded-lg border border-rose-200 bg-white px-3 text-sm font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50"><Square size={14} />{cancelAutoProductionMutation.isPending ? "Đang dừng..." : "Dừng tự động"}</button> : <button onClick={() => autoProductionMutation.mutate()} disabled={autoProductionMutation.isPending || !scenes.length} title={!scenes.length ? "Sinh kịch bản trước" : undefined} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-green-700 px-3.5 text-sm font-semibold text-white hover:bg-green-800 disabled:opacity-50"><Film size={16} />{autoProductionMutation.isPending ? "Đang xếp hàng..." : "Tạo toàn bộ & Ghép MP4"}</button>}
          <button
            onClick={() => {
              if (confirm("Xóa toàn bộ dự án này? Hành động không thể hoàn tác.")) {
                deleteProjectMutation.mutate();
              }
            }}
            className="inline-flex items-center gap-1.5 rounded-lg border border-rose-200 bg-rose-50 hover:bg-rose-100 px-3 py-2.5 text-xs font-medium text-rose-600 transition"
          >
            <Trash2 className="w-3.5 h-3.5" /> Xóa dự án
          </button>
        </div>
      </div>

      {aspectMismatchScenes.length > 0 && <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3" role="alert">
        <p className="text-sm font-semibold text-amber-900">Tỷ lệ clip không khớp canvas {project.aspect_ratio}</p>
        <p className="mt-1 text-xs text-amber-800">{aspectMismatchScenes.length} cảnh hiện có video dọc trong project ngang, nên render đang thêm viền đen hai bên. Đã chọn model theo tỷ lệ project; bấm “Tạo toàn bộ &amp; Ghép MP4” để tự tạo lại clip sai tỷ lệ rồi ghép lại.</p>
      </div>}

      {autoProductionJob && <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4" role="status" aria-live="polite">
        <div className="flex items-center justify-between gap-3"><div><p className="text-sm font-semibold text-emerald-900">Đang tự động sản xuất toàn bộ cảnh</p><p className="mt-1 text-xs text-emerald-800">{autoProductionJob.status_message || "Đang xếp hàng"}</p></div><span className="text-sm font-semibold text-emerald-800">{autoProductionJob.progress_percent}%</span></div>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-emerald-100"><div className="h-full rounded-full bg-emerald-600 transition-all" style={{ width: `${Math.max(2, autoProductionJob.progress_percent)}%` }} /></div>
      </div>}

      {failedJobs.length > 0 && <section className="rounded-xl border border-rose-200 bg-rose-50 p-4" role="alert">
        <h2 className="text-sm font-semibold text-rose-900">Có {failedJobs.length} action bị lỗi</h2>
        <div className="mt-2 space-y-2">
          {failedJobs.map((job) => <div key={job.id} className="rounded-lg border border-rose-100 bg-white/70 p-3">
            <p className="text-xs font-semibold text-rose-900">{job.job_type} · {job.provider_model}</p>
            <p className="mt-1 text-xs text-rose-800">{job.error_message || job.status_message || "Không có thông tin lỗi"}</p>
            {job.error_code && <p className="mt-1 font-mono text-[10px] text-rose-600">Mã lỗi: {job.error_code}</p>}
          </div>)}
        </div>
      </section>}

      {/* Script / Scenes Section */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-4 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
            <FileText className="w-4 h-4 text-green-600" />
            Kịch bản & Phân cảnh ({scenes.length} cảnh)
          </h2>
          <div className="flex items-center gap-3">
            {scenes.length > 0 && (
              <label className="flex items-center gap-1.5 text-[11px] text-slate-500">
                <input type="checkbox" checked={forceOverwriteLocked} onChange={(e) => setForceOverwriteLocked(e.target.checked)} />
                Ghi đè cả cảnh đã khoá/đã duyệt
              </label>
            )}
            <button
              onClick={() => generateScriptMutation.mutate()}
              disabled={generateScriptMutation.isPending}
              className="inline-flex items-center gap-1.5 rounded-xl bg-green-600 hover:bg-green-700 disabled:opacity-50 px-3.5 py-2 text-xs font-semibold text-white shadow-sm shadow-green-600/20 transition"
            >
              {generateScriptMutation.isPending
                ? "Đang sinh..."
                : scenes.length > 0
                ? "Sinh lại kịch bản"
                : "Sinh kịch bản mới"}
            </button>
          </div>
        </div>

        {scenes.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-200 p-8 text-center text-sm text-slate-500">
            Chưa có kịch bản. Bấm "Sinh kịch bản mới" để tự động chia cảnh và viết lời thoại.
          </div>
        ) : (
          <>
            <div className="rounded-xl border border-green-200 bg-green-50 px-3.5 py-2.5 text-xs text-green-800">
              <strong>Continuity Gate:</strong> phải sinh ảnh & duyệt Scene 01 (Master Image) trước, rồi mới sinh được ảnh cho các Scene tiếp theo — để giữ nhân vật/bối cảnh đồng nhất xuyên suốt video.
            </div>
            <div className="space-y-3">
              {scenes.map((scene) => (
                <SceneCard
                  key={scene.id}
                  scene={scene}
                  autoApprove={project.auto_approve_master}
                  masterReady={masterReady}
                  onGenImage={() => genImageMutation.mutate(scene.id)}
                  onApprove={() => approveMutation.mutate(scene.id)}
                  onGenVideo={() => genVideoMutation.mutate(scene.id)}
                  genImagePending={genImageMutation.isPending && genImageMutation.variables === scene.id}
                  approvePending={approveMutation.isPending && approveMutation.variables === scene.id}
                  genVideoPending={genVideoMutation.isPending && genVideoMutation.variables === scene.id}
                />
              ))}
            </div>
          </>
        )}
      </div>

      {/* Render Section */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-4 shadow-sm">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
            <Film className="w-4 h-4 text-green-600" />
            Ghép video hoàn chỉnh (FFmpeg)
          </h2>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-[11px] text-slate-500">
              <input type="checkbox" checked={burnSubtitles} onChange={(e) => setBurnSubtitles(e.target.checked)} />
              Chèn phụ đề vào video
            </label>
            <label className="flex items-center gap-1.5 text-[11px] text-slate-500">
              <input type="checkbox" checked={colorGrade} onChange={(e) => setColorGrade(e.target.checked)} />
              Đồng bộ tông màu
            </label>
            <button
              onClick={() => renderMutation.mutate()}
              disabled={renderMutation.isPending || !allVideosReady}
              title={!allVideosReady ? "Mọi cảnh cần có video trước khi ghép" : undefined}
              className="inline-flex items-center gap-1.5 rounded-xl bg-green-600 hover:bg-green-700 disabled:opacity-40 px-3.5 py-2 text-xs font-semibold text-white shadow-sm shadow-green-600/20 transition"
            >
              {renderMutation.isPending ? "Đang ghép..." : "Ghép video ngay"}
            </button>
          </div>
        </div>

        {(rendersQuery.data || []).length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-200 p-6 text-center text-xs text-slate-500">
            Chưa có bản render nào. {allVideosReady ? "" : "Hoàn tất video cho mọi cảnh trước."}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {(rendersQuery.data || []).map((r) => (
              <div key={r.id} className="rounded-xl border border-slate-200 bg-slate-50 overflow-hidden">
                <div className="bg-black aspect-[9/16] max-h-64 flex items-center justify-center">
                  {r.mp4_url ? (
                    <video src={r.mp4_url} controls preload="metadata" className="w-full h-full object-contain" />
                  ) : (
                    <span className="text-xs text-slate-400 uppercase font-mono">{r.status}</span>
                  )}
                </div>
                <div className="p-3 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-semibold text-slate-700">v{r.version}</span>
                    <StatusBadge status={r.status} />
                  </div>
                  <p className="text-[11px] text-slate-400">
                    {r.resolution} • {r.duration_seconds.toFixed(1)}s • {formatBytes(r.file_size_bytes)}
                  </p>
                  {r.error_message && <p className="text-[11px] text-rose-600">{r.error_message}</p>}
                  {r.mp4_url && (
                    <a
                      href={`${r.mp4_url}${r.mp4_url.includes("?") ? "&" : "?"}download=1`}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-green-600 hover:bg-green-700 px-2.5 py-1.5 text-[11px] font-semibold text-white transition"
                    >
                      <Download className="w-3 h-3" /> Tải MP4
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SceneCard({
  scene,
  autoApprove,
  masterReady,
  onGenImage,
  onApprove,
  onGenVideo,
  genImagePending,
  approvePending,
  genVideoPending,
}: {
  scene: Scene;
  autoApprove: boolean;
  masterReady: boolean;
  onGenImage: () => void;
  onApprove: () => void;
  onGenVideo: () => void;
  genImagePending: boolean;
  approvePending: boolean;
  genVideoPending: boolean;
}) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const hasImage = !!scene.image_url;
  const isApproved = scene.approval_status === "approved" || autoApprove;
  const hasVideo = !!scene.video_url;
  const gateBlocked = !scene.is_master_scene && !masterReady;

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 flex flex-col md:flex-row gap-4">
      <div className="flex gap-3 shrink-0">
        <div className="w-24 h-32 rounded-lg bg-slate-100 border border-slate-200 overflow-hidden flex items-center justify-center shrink-0">
          {scene.video_url ? (
            <button type="button" onClick={() => setPreviewOpen(true)} className="group relative w-full h-full" aria-label={`Phát video ${scene.title}`}>
              <video src={scene.video_url} className="w-full h-full object-cover" muted preload="metadata" />
              <span className="absolute inset-0 grid place-items-center bg-black/20 text-white opacity-0 transition group-hover:opacity-100"><Video size={22} /></span>
            </button>
          ) : scene.image_url ? (
            <img src={scene.image_url} alt={scene.title} className="w-full h-full object-cover" />
          ) : (
            <ImagePlus className="w-6 h-6 text-slate-300" />
          )}
        </div>
      </div>

      <div className="flex-1 min-w-0 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-mono text-slate-400">#{scene.scene_index}</span>
          <h3 className="text-sm font-bold text-slate-900">{scene.title}</h3>
          {scene.is_master_scene && (
            <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-green-50 text-green-700 border border-green-200 flex items-center gap-1">
              <Lock className="w-2.5 h-2.5" /> Master
            </span>
          )}
          <StatusBadge status={scene.status} />
          {isApproved && (
            <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-green-50 text-green-700 border border-green-200">
              Đã duyệt
            </span>
          )}
        </div>
        <p className="text-xs text-slate-600 line-clamp-2">{scene.dialogue}</p>
        <p className="text-[11px] text-slate-400">
          {scene.character_name || "—"} • {scene.background_name || "—"} • {scene.planned_duration_sec}s
          {scene.speech_warning && <span className="text-amber-600"> • {scene.speech_warning}</span>}
        </p>

        {gateBlocked && (
          <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1 inline-block">
            Cần sinh &amp; duyệt ảnh Scene 01 (Master Image) trước khi sinh ảnh cho cảnh này.
          </p>
        )}

        <div className="flex items-center gap-2 pt-1 flex-wrap">
          <button
            onClick={onGenImage}
            disabled={genImagePending || gateBlocked}
            title={gateBlocked ? "Sinh & duyệt ảnh Scene 01 (Master Image) trước" : undefined}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed px-2.5 py-1.5 text-[11px] font-medium text-slate-700 transition"
          >
            <ImagePlus className="w-3 h-3 text-green-600" />
            {genImagePending ? "Đang sinh ảnh..." : hasImage ? "Sinh lại ảnh" : "Sinh ảnh"}
          </button>

          {hasImage && !isApproved && (
            <button
              onClick={onApprove}
              disabled={approvePending}
              className="inline-flex items-center gap-1.5 rounded-lg bg-green-600 hover:bg-green-700 disabled:opacity-50 px-2.5 py-1.5 text-[11px] font-semibold text-white transition"
            >
              <CheckCircle2 className="w-3 h-3" />
              {approvePending ? "Đang duyệt..." : "Duyệt Master Image"}
            </button>
          )}

          {hasImage && isApproved && (
            <button
              onClick={onGenVideo}
              disabled={genVideoPending}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-100 disabled:opacity-50 px-2.5 py-1.5 text-[11px] font-medium text-slate-700 transition"
            >
              <Video className="w-3 h-3 text-green-600" />
              {genVideoPending ? "Đang sinh video..." : hasVideo ? "Sinh lại video" : "Sinh video"}
            </button>
          )}
        </div>
      </div>
      {previewOpen && scene.video_url && (
        <div className="fixed inset-0 z-[100] bg-slate-950/80 p-4 md:p-8 grid place-items-center" role="dialog" aria-modal="true" aria-label={`Video ${scene.title}`} onClick={() => setPreviewOpen(false)}>
          <div className="w-full max-w-5xl" onClick={(event) => event.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between text-white"><div><p className="text-sm font-semibold">Scene {scene.scene_index} · {scene.title}</p><p className="text-xs text-white/65">Video clip đã tạo</p></div><button type="button" onClick={() => setPreviewOpen(false)} className="rounded-lg p-2 hover:bg-white/10" aria-label="Đóng video"><X size={20} /></button></div>
            <video src={scene.video_url} controls autoPlay playsInline className="max-h-[78vh] w-full rounded-xl bg-black" />
          </div>
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles =
    status === "completed" || status === "video_ready" || status === "approved" || status === "image_ready"
      ? "bg-green-50 text-green-700 border-green-200"
      : status === "failed"
      ? "bg-rose-50 text-rose-700 border-rose-200"
      : status.includes("queued") || status.includes("generating")
      ? "bg-sky-50 text-sky-700 border-sky-200"
      : "bg-slate-100 text-slate-600 border-slate-200";
  return <span className={cn("px-2 py-0.5 rounded-full text-[10px] font-medium border", styles)}>{status}</span>;
}
