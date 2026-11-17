"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderKanban, Plus, Trash2, Play, Pause, Square } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useToastStore } from "@/lib/store";
import type { AIModelEntry, Background, Batch, Character, Project, ProviderSetting } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function ProjectsPage() {
  const qc = useQueryClient();
  const pushToast = useToastStore((s) => s.push);

  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [language, setLanguage] = useState("vi-VN");
  const [scriptMode, setScriptMode] = useState<"ai_presenter" | "literal">("ai_presenter");
  const [voiceMode, setVoiceMode] = useState<"ai_generated" | "external_tts" | "silent">("ai_generated");
  const [voiceDescription, setVoiceDescription] = useState("Giọng tiếng Việt tự nhiên, rõ ràng, phát âm chuẩn; tốc độ vừa phải.");
  const [goalDescription, setGoalDescription] = useState("");
  const [durationSec, setDurationSec] = useState(24);
  const [aspect, setAspect] = useState<"9:16" | "16:9" | "1:1">("9:16");
  const [visualStyle, setVisualStyle] = useState("photorealistic");
  const [charId, setCharId] = useState("");
  const [bgId, setBgId] = useState("");
  const [imageModel, setImageModel] = useState("gemini-3.1-flash-image-portrait-2k");
  const [videoModel, setVideoModel] = useState("omni-1.2-flash-8s-portrait");
  const [autoApprove, setAutoApprove] = useState(false);
  const [batchProjectId, setBatchProjectId] = useState("");
  const [batchName, setBatchName] = useState("");
  const [batchCount, setBatchCount] = useState(5);

  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: () => apiFetch<Project[]>("/projects"),
  });
  const batchesQuery = useQuery({ queryKey: ["batches"], queryFn: () => apiFetch<Batch[]>("/batches"), refetchInterval: 5000 });

  const charsQuery = useQuery({
    queryKey: ["characters"],
    queryFn: () => apiFetch<Character[]>("/characters"),
  });

  const bgsQuery = useQuery({
    queryKey: ["backgrounds"],
    queryFn: () => apiFetch<Background[]>("/backgrounds"),
  });

  const modelsQuery = useQuery({
    queryKey: ["models"],
    queryFn: () =>
      apiFetch<{ source: string; has_api_key: boolean; models: AIModelEntry[] }>("/providers/models"),
  });
  const settingsQuery = useQuery({ queryKey: ["provider-settings"], queryFn: () => apiFetch<ProviderSetting[]>("/providers/settings") });

  useEffect(() => {
    const worker = settingsQuery.data?.find((setting) => setting.provider_key === "worker");
    if (!worker) return;
    if (typeof worker.config_json.default_language === "string") setLanguage(worker.config_json.default_language);
    if (worker.config_json.default_voice_mode === "ai_generated" || worker.config_json.default_voice_mode === "external_tts" || worker.config_json.default_voice_mode === "silent") setVoiceMode(worker.config_json.default_voice_mode);
    if (typeof worker.config_json.default_voice_description === "string") setVoiceDescription(worker.config_json.default_voice_description);
  }, [settingsQuery.data]);

  const imageModels = (modelsQuery.data?.models || []).filter((m) => m.type === "image");
  const videoModels = (modelsQuery.data?.models || []).filter((m) => m.type === "video");

  function changeAspect(next: typeof aspect) {
    setAspect(next);
    const direction = next === "16:9" ? "landscape" : next === "1:1" ? "square" : "portrait";
    const imageMatch = imageModels.find((model) => model.id.toLowerCase().includes(direction));
    // Veo R2V/I2V tiers are not reliably available on every Flow2API account (often
    // returns PUBLIC_ERROR_MODEL_ACCESS_DENIED); Omni is the broadly-available default.
    const preferredVideoModel = next === "16:9"
      ? videoModels.find((model) => model.id === "omni-1.2-flash-8s-landscape")
      : videoModels.find((model) => model.id === "omni-1.2-flash-8s-portrait");
    const videoMatch = preferredVideoModel || videoModels.find((model) => model.id.toLowerCase().includes(direction));
    if (imageMatch) setImageModel(imageMatch.id);
    if (videoMatch) setVideoModel(videoMatch.id);
  }

  const createMutation = useMutation({
    mutationFn: () =>
      apiFetch<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          topic: topic.trim(),
          language,
          voice_mode: voiceMode,
          script_mode: scriptMode,
          voice_description: voiceDescription,
          goal_description: goalDescription.trim(),
          target_duration_seconds: durationSec,
          aspect_ratio: aspect,
          visual_style: visualStyle,
          default_character_id: charId || null,
          default_background_id: bgId || null,
          image_model: imageModel.trim(),
          video_model: videoModel.trim(),
          auto_approve_master: autoApprove,
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setName("");
      setTopic("");
      setGoalDescription("");
      pushToast({ title: "Đã tạo dự án mới", variant: "success" });
    },
    onError: (err: unknown) => {
      pushToast({
        title: "Không thể tạo dự án",
        description: err instanceof Error ? err.message : "Lỗi không xác định",
        variant: "error",
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/projects/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      pushToast({ title: "Đã xóa dự án", variant: "info" });
    },
  });

  const createBatchMutation = useMutation({
    mutationFn: () => apiFetch<Batch>("/batches", { method: "POST", body: JSON.stringify({ project_id: batchProjectId, name: batchName.trim(), total_videos: batchCount }) }),
    onSuccess: (batch) => { qc.invalidateQueries({ queryKey: ["batches"] }); setBatchName(""); pushToast({ title: "Đã tạo lô sản xuất", description: batch.name, variant: "success" }); },
    onError: (err: unknown) => pushToast({ title: "Không thể tạo lô", description: err instanceof Error ? err.message : "Lỗi không xác định", variant: "error" }),
  });
  const batchActionMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "start" | "pause" | "resume" | "cancel" }) => apiFetch<Batch>(`/batches/${id}/${action}`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["batches"] }),
    onError: (err: unknown) => pushToast({ title: "Không thực hiện được thao tác", description: err instanceof Error ? err.message : "Lỗi không xác định", variant: "error" }),
  });

  return (
    <div className="p-6 lg:p-8 max-w-7xl w-full mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5">
          <FolderKanban className="w-6 h-6 text-green-600" />
          Dự án & Sản xuất Hàng loạt
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          Tạo dự án mới, sau đó sinh kịch bản, duyệt Master Image và tạo video từng cảnh trong Studio.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Create Project Form */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!name.trim() || !topic.trim()) return;
            createMutation.mutate();
          }}
          className="rounded-2xl border border-slate-200 bg-white p-5 space-y-4 h-fit shadow-sm"
        >
          <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
            <Plus className="w-4 h-4 text-green-600" />
            Tạo dự án mới
          </h2>

          <div>
            <div className="mb-1 flex items-center justify-between"><label className="block text-xs text-slate-500">Tên dự án *</label><span className={`text-xs ${name.length >= 240 ? "text-amber-700" : "text-slate-400"}`}>{name.length}/255</span></div>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="VD: Forex XAUUSD Education"
              maxLength={255}
              required
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 focus:border-green-500 focus:ring-2 focus:ring-green-500/15 focus:outline-none"
            />
          </div>

          <div className="grid grid-cols-2 gap-2 rounded-xl border border-slate-200 bg-slate-50 p-1 text-xs">
            <button
              type="button"
              onClick={() => setScriptMode("ai_presenter")}
              className={cn("rounded-lg px-3 py-2 text-left transition", scriptMode === "ai_presenter" ? "bg-white shadow-sm font-medium text-slate-900" : "text-slate-500 hover:text-slate-700")}
            >
              Thuyết trình (AI tự viết lời)
              <div className="mt-0.5 text-[11px] font-normal text-slate-400">Nhập một chủ đề, AI viết kịch bản + lời thoại để giải thích nó.</div>
            </button>
            <button
              type="button"
              onClick={() => setScriptMode("literal")}
              className={cn("rounded-lg px-3 py-2 text-left transition", scriptMode === "literal" ? "bg-white shadow-sm font-medium text-slate-900" : "text-slate-500 hover:text-slate-700")}
            >
              Theo kịch bản nhập vào (giữ nguyên văn)
              <div className="mt-0.5 text-[11px] font-normal text-slate-400">Dùng đúng mô tả cảnh/lời bạn gõ, không tự viết thêm hay đổi thành "đang giải thích".</div>
            </button>
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between"><label htmlFor="project-topic" className="block text-xs text-slate-500">{scriptMode === "literal" ? "Kịch bản / Mô tả cảnh quay (nguyên văn) *" : "Chủ đề / Kịch bản *"}</label><span className={`text-xs ${topic.length >= 950 ? "text-amber-700" : "text-slate-400"}`}>{topic.length}/1000</span></div>
            <input
              id="project-topic"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder={scriptMode === "literal" ? "VD: Người đàn ông ngồi trước máy tính, mệt mỏi, ngáp, rồi gục đầu ngủ..." : "VD: Smart Money Concepts Liquidity Sweep"}
              maxLength={1000}
              required
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900"
            />
            {scriptMode === "literal" && (
              <p className="mt-1 text-xs text-slate-400">Toàn bộ nội dung trên sẽ được chia đúng theo trình tự cho từng cảnh. Nếu chọn "Không lời thoại" ở Nguồn giọng, nó trở thành hành động hình ảnh; nếu có giọng, nó sẽ được đọc nguyên văn.</p>
            )}
          </div>

          <div>
            <label className="block text-xs text-slate-500 mb-1">Mục tiêu (tùy chọn)</label>
            <input
              value={goalDescription}
              onChange={(e) => setGoalDescription(e.target.value)}
              placeholder="Thông điệp chính muốn truyền tải..."
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Thời lượng (giây)</label>
              <input
                type="number"
                min={4}
                max={600}
                value={durationSec}
                onChange={(e) => setDurationSec(Number(e.target.value))}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Tỷ lệ khung hình</label>
              <select
                value={aspect}
                onChange={(e) => changeAspect(e.target.value as typeof aspect)}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
              >
                <option value="9:16">9:16 (Dọc / Shorts)</option>
                <option value="16:9">16:9 (Ngang)</option>
                <option value="1:1">1:1 (Vuông)</option>
              </select>
            </div>
          </div>

          <div>
            <label htmlFor="project-language" className="block text-xs text-slate-500 mb-1">Ngôn ngữ lời thoại</label>
            <select id="project-language" value={language} onChange={(e) => setLanguage(e.target.value)} className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900">
              <option value="vi-VN">Tiếng Việt</option>
              <option value="en-US">English (US)</option>
              <option value="en-GB">English (UK)</option>
              <option value="ja-JP">日本語</option>
              <option value="ko-KR">한국어</option>
              <option value="zh-CN">中文（普通话）</option>
            </select>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div><label htmlFor="project-voice-mode" className="block text-xs text-slate-500 mb-1">Nguồn giọng</label><select id="project-voice-mode" value={voiceMode} onChange={(e) => setVoiceMode(e.target.value as typeof voiceMode)} className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"><option value="ai_generated">Giọng tạo cùng video (lip-sync)</option><option value="external_tts">Âm thanh TTS tải lên</option><option value="silent">Không lời thoại (mô tả cảnh/hành động tự do)</option></select></div>
            <div><label htmlFor="project-voice-description" className="block text-xs text-slate-500 mb-1">Mô tả giọng đọc</label><input id="project-voice-description" value={voiceDescription} onChange={(e) => setVoiceDescription(e.target.value)} maxLength={500} disabled={voiceMode === "silent"} className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm disabled:bg-slate-50 disabled:text-slate-400" /></div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Nhân vật mặc định</label>
              <select
                value={charId}
                onChange={(e) => setCharId(e.target.value)}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
              >
                <option value="">— Không chọn —</option>
                {(charsQuery.data || []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Background mặc định</label>
              <select
                value={bgId}
                onChange={(e) => setBgId(e.target.value)}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
              >
                <option value="">— Không chọn —</option>
                {(bgsQuery.data || []).map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label className="block text-xs text-slate-500 mb-1">Image Model (Nano Banana 2.1)</label>
            {imageModels.length > 0 ? (
              <select
                value={imageModel}
                onChange={(e) => setImageModel(e.target.value)}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900 font-mono"
              >
                {imageModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.id}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={imageModel}
                onChange={(e) => setImageModel(e.target.value)}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900 font-mono"
              />
            )}
          </div>

          <div>
            <label className="block text-xs text-slate-500 mb-1">Video Model (Omni 1.2 / Veo 3.1)</label>
            {videoModels.length > 0 ? (
              <select
                value={videoModel}
                onChange={(e) => setVideoModel(e.target.value)}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900 font-mono"
              >
                {videoModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.id}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={videoModel}
                onChange={(e) => setVideoModel(e.target.value)}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900 font-mono"
              />
            )}
          </div>

          <button
            type="button"
            onClick={() => setAutoApprove((v) => !v)}
            className="w-full flex items-center gap-2 text-left text-xs text-slate-600 hover:text-slate-900"
          >
            <span
              className={cn(
                "w-4 h-4 rounded flex items-center justify-center border",
                autoApprove ? "bg-green-600 border-green-600" : "border-slate-300 bg-white"
              )}
            />
            Tự động duyệt Master Image (bỏ qua Continuity Gate)
          </button>

          <button
            type="submit"
            disabled={createMutation.isPending}
            className="w-full rounded-xl bg-green-600 hover:bg-green-700 disabled:opacity-50 py-2.5 text-xs font-semibold text-white shadow-sm shadow-green-600/20 transition"
          >
            {createMutation.isPending ? "Đang tạo..." : "Tạo Dự án"}
          </button>
        </form>

        {/* Project List */}
        <div className="lg:col-span-2 space-y-3 h-fit">
          {(projectsQuery.data || []).length === 0 ? (
            <div className="rounded-2xl border border-dashed border-slate-200 p-12 text-center text-sm text-slate-500">
              Chưa có dự án nào. Hãy tạo dự án đầu tiên ở bên trái.
            </div>
          ) : (
            (projectsQuery.data || []).map((p) => (
              <div
                key={p.id}
                className="rounded-2xl border border-slate-200 bg-white p-4 flex items-center justify-between gap-4 shadow-sm"
              >
                <Link href={`/projects/${p.id}`} className="min-w-0 flex-1">
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
                </Link>
                <div className="flex items-center gap-2 shrink-0">
                  <Link
                    href={`/projects/${p.id}`}
                    className="inline-flex items-center rounded-lg border border-slate-200 bg-white hover:bg-slate-50 px-3 py-1.5 text-xs font-medium text-slate-700"
                  >
                    Mở Studio →
                  </Link>
                  <button
                    onClick={() => deleteMutation.mutate(p.id)}
                    className="text-slate-400 hover:text-rose-500 p-1.5"
                    title="Xóa dự án"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <section className="rounded-xl border border-slate-200 bg-white p-5 space-y-4">
        <div><h2 className="text-base font-semibold text-slate-900">Sản xuất hàng loạt</h2><p className="text-sm text-slate-500 mt-1">Tạo nhiều biến thể video từ một dự án; có thể tạm dừng hoặc dừng lô.</p></div>
        <form onSubmit={(event) => { event.preventDefault(); if (batchProjectId && batchName.trim()) createBatchMutation.mutate(); }} className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-[1fr_1fr_140px_auto] gap-3 items-end">
          <label className="text-xs text-slate-500">Dự án<select required value={batchProjectId} onChange={(event) => setBatchProjectId(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800"><option value="">Chọn dự án</option>{(projectsQuery.data || []).map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
          <label className="text-xs text-slate-500">Tên lô<input required value={batchName} onChange={(event) => setBatchName(event.target.value)} placeholder="Ví dụ: Series tuần này" className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm" /></label>
          <label className="text-xs text-slate-500">Số video<input type="number" min={1} max={200} value={batchCount} onChange={(event) => setBatchCount(Number(event.target.value))} className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm" /></label>
          <button disabled={!batchProjectId || !batchName.trim() || createBatchMutation.isPending} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-green-700 px-4 text-sm font-semibold text-white hover:bg-green-800 disabled:opacity-50"><Plus size={16} />{createBatchMutation.isPending ? "Đang tạo..." : "Tạo lô"}</button>
        </form>
        {(batchesQuery.data || []).length > 0 && <div className="divide-y divide-slate-100 border-t border-slate-100">{(batchesQuery.data || []).map((batch) => <div key={batch.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
          <div className="min-w-[200px]"><p className="text-sm font-medium text-slate-900">{batch.name}</p><p className="text-xs text-slate-500">{batch.project_name || "Dự án"} · {batch.completed_videos}/{batch.total_videos} hoàn tất · {batch.failed_videos} lỗi</p></div>
          <div className="flex items-center gap-2"><span className="rounded-md bg-slate-100 px-2 py-1 text-xs text-slate-600">{batch.status}</span>
            {(batch.status === "draft" || batch.status === "queued") && <button onClick={() => batchActionMutation.mutate({ id: batch.id, action: "start" })} className="inline-flex h-9 items-center gap-1 rounded-lg border px-3 text-xs hover:bg-slate-50"><Play size={14} />Bắt đầu</button>}
            {batch.status === "running" && <button onClick={() => batchActionMutation.mutate({ id: batch.id, action: "pause" })} className="inline-flex h-9 items-center gap-1 rounded-lg border px-3 text-xs hover:bg-slate-50"><Pause size={14} />Tạm dừng</button>}
            {batch.status === "paused" && <button onClick={() => batchActionMutation.mutate({ id: batch.id, action: "resume" })} className="inline-flex h-9 items-center gap-1 rounded-lg border px-3 text-xs hover:bg-slate-50"><Play size={14} />Tiếp tục</button>}
            {["draft", "queued", "running", "paused"].includes(batch.status) && <button onClick={() => { if (confirm(`Dừng lô “${batch.name}”?`)) batchActionMutation.mutate({ id: batch.id, action: "cancel" }); }} className="inline-flex h-9 items-center gap-1 rounded-lg border border-rose-200 px-3 text-xs text-rose-700 hover:bg-rose-50"><Square size={13} />Dừng lô</button>}
          </div>
        </div>)}</div>}
      </section>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const styles =
    status === "completed" || status === "script_ready" || status === "rendered"
      ? "bg-green-50 text-green-700 border-green-200"
      : status === "failed"
      ? "bg-rose-50 text-rose-700 border-rose-200"
      : status.includes("generating")
      ? "bg-sky-50 text-sky-700 border-sky-200"
      : "bg-slate-100 text-slate-600 border-slate-200";
  return (
    <span className={cn("px-2 py-0.5 rounded-full text-[10px] font-medium border", styles)}>{status}</span>
  );
}
