"use client";

import React, { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Settings, PlugZap, RefreshCw, Save, CheckCircle2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useToastStore } from "@/lib/store";
import type { ProviderSetting } from "@/lib/types";

interface ProviderTestResult {
  provider_key: string;
  ok: boolean;
  message?: string;
  status_code?: number;
  error_code?: string;
  total_models?: number;
  image_models?: number;
  video_models?: number;
  sample_models?: string[];
}

function describeTestResult(res: ProviderTestResult): string {
  if (res.ok) {
    if (typeof res.total_models === "number") {
      return `Tìm thấy ${res.total_models} model (${res.image_models ?? 0} ảnh, ${res.video_models ?? 0} video)`;
    }
    if (typeof res.status_code === "number") {
      return `Phản hồi HTTP ${res.status_code} — LLM hoạt động bình thường`;
    }
    return "Kết nối thành công";
  }
  return res.message || (res.error_code ? `Lỗi: ${res.error_code}` : "Kết nối thất bại, kiểm tra lại Base URL / API Key");
}

const LLM_PRESETS: Record<string, { base_url: string; model: string }> = {
  openai_compatible: { base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  deepseek: { base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  gemini: { base_url: "https://generativelanguage.googleapis.com/v1beta", model: "gemini-1.5-flash" },
};

export default function SettingsPage() {
  const qc = useQueryClient();
  const pushToast = useToastStore((s) => s.push);

  const settingsQuery = useQuery({
    queryKey: ["provider-settings"],
    queryFn: () => apiFetch<ProviderSetting[]>("/providers/settings"),
  });

  const [flowBaseUrl, setFlowBaseUrl] = useState("https://flow.aetrading.vn/v1");
  const [flowApiKey, setFlowApiKey] = useState("");
  const [imgTimeout, setImgTimeout] = useState(180);
  const [vidTimeout, setVidTimeout] = useState(900);

  const [llmProvider, setLlmProvider] = useState("openai_compatible");
  const [llmBaseUrl, setLlmBaseUrl] = useState("https://api.openai.com/v1");
  const [llmModel, setLlmModel] = useState("gpt-4o-mini");
  const [llmApiKey, setLlmApiKey] = useState("");
  const [defaultLanguage, setDefaultLanguage] = useState("vi-VN");
  const [defaultVoiceMode, setDefaultVoiceMode] = useState<"ai_generated" | "external_tts" | "silent">("ai_generated");
  const [defaultVoiceDescription, setDefaultVoiceDescription] = useState("Giọng tiếng Việt tự nhiên, rõ ràng, phát âm chuẩn; tốc độ vừa phải.");

  useEffect(() => {
    if (!settingsQuery.data) return;
    const flow = settingsQuery.data.find((s) => s.provider_key === "flow2api");
    if (flow) {
      if (flow.base_url) setFlowBaseUrl(flow.base_url);
      if (flow.config_json?.image_timeout) setImgTimeout(Number(flow.config_json.image_timeout));
      if (flow.config_json?.video_timeout) setVidTimeout(Number(flow.config_json.video_timeout));
    }
    const llm = settingsQuery.data.find((s) => s.provider_key === "script_llm");
    if (llm) {
      if (llm.base_url) setLlmBaseUrl(llm.base_url);
      if (llm.model_default) setLlmModel(llm.model_default);
      if (llm.config_json?.provider) setLlmProvider(String(llm.config_json.provider));
    }
    const worker = settingsQuery.data.find((s) => s.provider_key === "worker");
    if (worker) {
      if (typeof worker.config_json.default_language === "string") setDefaultLanguage(worker.config_json.default_language);
      if (worker.config_json.default_voice_mode === "ai_generated" || worker.config_json.default_voice_mode === "external_tts" || worker.config_json.default_voice_mode === "silent") setDefaultVoiceMode(worker.config_json.default_voice_mode);
      if (typeof worker.config_json.default_voice_description === "string") setDefaultVoiceDescription(worker.config_json.default_voice_description);
    }
  }, [settingsQuery.data]);

  function handleProviderChange(next: string) {
    setLlmProvider(next);
    const preset = LLM_PRESETS[next];
    if (preset) {
      setLlmBaseUrl(preset.base_url);
      setLlmModel(preset.model);
    }
  }

  const saveFlowMutation = useMutation({
    mutationFn: () =>
      apiFetch<ProviderSetting>("/providers/settings/flow2api", {
        method: "PUT",
        body: JSON.stringify({
          base_url: flowBaseUrl.trim(),
          api_key: flowApiKey.trim() || undefined,
          config_json: {
            image_timeout: imgTimeout,
            video_timeout: vidTimeout,
          },
          is_active: true,
        }),
      }),
    onSuccess: () => {
      setFlowApiKey("");
      qc.invalidateQueries({ queryKey: ["provider-settings"] });
      qc.invalidateQueries({ queryKey: ["models"] });
      pushToast({ title: "Đã lưu cấu hình Flow2API (mã hóa AES/CTR)", variant: "success" });
    },
    onError: (err: unknown) => {
      pushToast({
        title: "Lỗi lưu cấu hình",
        description: err instanceof Error ? err.message : "Không thể cập nhật",
        variant: "error",
      });
    },
  });

  const testFlowMutation = useMutation({
    mutationFn: () =>
      apiFetch<ProviderTestResult>("/providers/test", {
        method: "POST",
        body: JSON.stringify({ provider_key: "flow2api" }),
      }),
    onSuccess: (res) => {
      pushToast({
        title: res.ok ? "Kết nối Flow2API thành công!" : "Kết nối Flow2API thất bại",
        description: describeTestResult(res),
        variant: res.ok ? "success" : "error",
      });
    },
    onError: (err: unknown) => {
      pushToast({
        title: "Không thể kết nối Flow2API",
        description: err instanceof Error ? err.message : "Kiểm tra lại API Key",
        variant: "error",
      });
    },
  });

  const refreshModelsMutation = useMutation({
    mutationFn: () => apiFetch<{ count: number }>("/providers/models/refresh", { method: "POST" }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["models"] });
      pushToast({
        title: "Đã đồng bộ danh sách model",
        description: `Tìm thấy ${res.count ?? 0} model từ Flow2API`,
        variant: "success",
      });
    },
    onError: (err: unknown) => {
      pushToast({
        title: "Không thể làm mới model",
        description: err instanceof Error ? err.message : "Chưa có key hoặc lỗi mạng",
        variant: "error",
      });
    },
  });

  const saveLlmMutation = useMutation({
    mutationFn: () =>
      apiFetch<ProviderSetting>("/providers/settings/script_llm", {
        method: "PUT",
        body: JSON.stringify({
          base_url: llmBaseUrl.trim(),
          api_key: llmApiKey.trim() || undefined,
          model_default: llmModel.trim(),
          config_json: { provider: llmProvider },
          is_active: true,
        }),
      }),
    onSuccess: () => {
      setLlmApiKey("");
      qc.invalidateQueries({ queryKey: ["provider-settings"] });
      pushToast({ title: "Đã lưu cấu hình AI Script LLM", variant: "success" });
    },
    onError: (err: unknown) => {
      pushToast({
        title: "Lỗi lưu cấu hình LLM",
        description: err instanceof Error ? err.message : "Không thể cập nhật",
        variant: "error",
      });
    },
  });

  const testLlmMutation = useMutation({
    mutationFn: () =>
      apiFetch<ProviderTestResult>("/providers/test", {
        method: "POST",
        body: JSON.stringify({ provider_key: "script_llm" }),
      }),
    onSuccess: (res) => {
      pushToast({
        title: res.ok ? "Kết nối Script LLM thành công!" : "Kết nối Script LLM thất bại",
        description: describeTestResult(res),
        variant: res.ok ? "success" : "error",
      });
    },
    onError: (err: unknown) => {
      pushToast({
        title: "Không thể kết nối Script LLM",
        description: err instanceof Error ? err.message : "Kiểm tra lại API Key — nhớ Lưu trước khi Test",
        variant: "error",
      });
    },
  });

  const saveDefaultsMutation = useMutation({
    mutationFn: () => apiFetch<ProviderSetting>("/providers/settings/worker", {
      method: "PUT",
      body: JSON.stringify({
        config_json: {
          default_language: defaultLanguage,
          default_voice_mode: defaultVoiceMode,
          default_voice_description: defaultVoiceDescription.trim(),
        },
        is_active: true,
      }),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["provider-settings"] });
      pushToast({ title: "Đã lưu ngôn ngữ và giọng mặc định", variant: "success" });
    },
    onError: (err: unknown) => pushToast({ title: "Không lưu được cài đặt mặc định", description: err instanceof Error ? err.message : "Lỗi không xác định", variant: "error" }),
  });

  const flowRow = settingsQuery.data?.find((s) => s.provider_key === "flow2api");
  const llmRow = settingsQuery.data?.find((s) => s.provider_key === "script_llm");

  return (
    <div className="p-6 lg:p-8 max-w-5xl w-full mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5">
          <Settings className="w-6 h-6 text-green-600" />
          Cấu hình Nhà cung cấp AI & Lưu trữ
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          Mọi khóa API đều được mã hóa trước khi lưu vào PostgreSQL và tự động che dấu trên giao diện.
        </p>
      </div>

      <section className="rounded-xl border border-slate-200 bg-white p-5 space-y-4">
        <div><h2 className="text-base font-semibold text-slate-900">Ngôn ngữ &amp; giọng mặc định cho dự án mới</h2><p className="mt-1 text-sm text-slate-500">Được áp dụng tự động khi tạo project mới; lời thoại và chỉ dẫn voice/lip-sync dùng cùng locale.</p></div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <label className="text-xs text-slate-500">Ngôn ngữ mặc định<select value={defaultLanguage} onChange={(e) => setDefaultLanguage(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800"><option value="vi-VN">Tiếng Việt</option><option value="en-US">English (US)</option><option value="en-GB">English (UK)</option><option value="ja-JP">日本語</option><option value="ko-KR">한국어</option><option value="zh-CN">中文（普通话）</option></select></label>
          <label className="text-xs text-slate-500">Chế độ giọng mặc định<select value={defaultVoiceMode} onChange={(e) => setDefaultVoiceMode(e.target.value as typeof defaultVoiceMode)} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800"><option value="ai_generated">Tạo giọng trong video (lip-sync)</option><option value="external_tts">Dùng audio TTS tải lên</option><option value="silent">Không lời thoại (mô tả cảnh/hành động tự do)</option></select></label>
          <label className="text-xs text-slate-500">Mô tả chất giọng<textarea value={defaultVoiceDescription} onChange={(e) => setDefaultVoiceDescription(e.target.value)} maxLength={500} rows={2} className="mt-1 w-full resize-y rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800" placeholder="Ví dụ: giọng nữ miền Nam, ấm, rõ, tốc độ vừa phải" /></label>
        </div>
        <button type="button" onClick={() => saveDefaultsMutation.mutate()} disabled={saveDefaultsMutation.isPending || !defaultVoiceDescription.trim()} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-green-700 px-4 text-sm font-semibold text-white hover:bg-green-800 disabled:opacity-50"><Save size={16} />{saveDefaultsMutation.isPending ? "Đang lưu..." : "Lưu mặc định"}</button>
      </section>

      {/* Flow2API Card */}
      <div className="rounded-2xl border border-slate-200 bg-white p-6 space-y-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-base font-bold text-slate-900">1. Flow2API (Nano Banana 2.1 + Omni 1.2 / Veo 3.1)</h2>
            <p className="text-xs text-slate-500">
              Trạng thái khóa hiện tại:{" "}
              <span className="font-mono text-green-700">
                {flowRow?.has_api_key ? `Đã cấu hình (${flowRow.api_key_masked})` : "Chưa nhập API Key"}
              </span>
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => testFlowMutation.mutate()}
              disabled={testFlowMutation.isPending}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 disabled:opacity-50 px-3 py-2 text-xs font-medium text-slate-700"
            >
              <PlugZap className="w-3.5 h-3.5 text-green-600" />
              {testFlowMutation.isPending ? "Đang kiểm tra..." : "Test kết nối"}
            </button>
            <button
              type="button"
              onClick={() => refreshModelsMutation.mutate()}
              disabled={refreshModelsMutation.isPending}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 disabled:opacity-50 px-3 py-2 text-xs font-medium text-slate-700"
            >
              <RefreshCw className="w-3.5 h-3.5 text-green-600" />
              Đồng bộ /v1/models
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-slate-500 mb-1">Flow2API Base URL</label>
            <input
              value={flowBaseUrl}
              onChange={(e) => setFlowBaseUrl(e.target.value)}
              className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900 font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Flow2API Bearer API Key (để trống nếu giữ nguyên)</label>
            <input
              type="password"
              value={flowApiKey}
              onChange={(e) => setFlowApiKey(e.target.value)}
              placeholder={flowRow?.has_api_key ? flowRow.api_key_masked : "Nhập FLOW2API_API_KEY..."}
              className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900 font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Image Timeout (giây — khuyến nghị ≥ 180s)</label>
            <input
              type="number"
              value={imgTimeout}
              onChange={(e) => setImgTimeout(Number(e.target.value))}
              className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Video Timeout (giây — khuyến nghị ≥ 900s)</label>
            <input
              type="number"
              value={vidTimeout}
              onChange={(e) => setVidTimeout(Number(e.target.value))}
              className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900"
            />
          </div>
        </div>

        <button
          type="button"
          onClick={() => saveFlowMutation.mutate()}
          disabled={saveFlowMutation.isPending}
          className="inline-flex items-center gap-2 rounded-xl bg-green-600 hover:bg-green-700 disabled:opacity-50 px-4 py-2.5 text-xs font-semibold text-white shadow-sm shadow-green-600/20 transition"
        >
          <Save className="w-4 h-4" />
          Lưu cấu hình Flow2API
        </button>
      </div>

      {/* Script LLM Card */}
      <div className="rounded-2xl border border-slate-200 bg-white p-6 space-y-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-base font-bold text-slate-900">2. AI Script Generator LLM (Độc lập với Flow2API)</h2>
            <p className="text-xs text-slate-500">
              Trạng thái khóa:{" "}
              <span className="font-mono text-green-700">
                {llmRow?.has_api_key
                  ? `Đã cấu hình (${llmRow.api_key_masked})`
                  : "Đang dùng Smart Scene Planner Fallback (chạy ngay không cần key)"}
              </span>
            </p>
          </div>
          <button
            type="button"
            onClick={() => testLlmMutation.mutate()}
            disabled={testLlmMutation.isPending}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 disabled:opacity-50 px-3 py-2 text-xs font-medium text-slate-700"
          >
            <PlugZap className="w-3.5 h-3.5 text-green-600" />
            {testLlmMutation.isPending ? "Đang kiểm tra..." : "Test kết nối"}
          </button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-slate-500 mb-1">Provider</label>
            <select
              value={llmProvider}
              onChange={(e) => handleProviderChange(e.target.value)}
              className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900"
            >
              <option value="openai_compatible">OpenAI-Compatible (/v1/chat/completions)</option>
              <option value="deepseek">DeepSeek (OpenAI-Compatible)</option>
              <option value="gemini">Google Gemini REST API</option>
              <option value="template_fallback">Offline Smart Scene Planner</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Model viết kịch bản</label>
            <input
              value={llmModel}
              onChange={(e) => setLlmModel(e.target.value)}
              className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900 font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Base URL</label>
            <input
              value={llmBaseUrl}
              onChange={(e) => setLlmBaseUrl(e.target.value)}
              className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900 font-mono"
            />
          </div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">LLM API Key</label>
            <input
              type="password"
              value={llmApiKey}
              onChange={(e) => setLlmApiKey(e.target.value)}
              placeholder={llmRow?.has_api_key ? llmRow.api_key_masked : "sk-..."}
              className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-900 font-mono"
            />
          </div>
        </div>
        {llmProvider === "deepseek" && (
          <p className="text-[11px] text-slate-400">
            Lấy API Key tại platform.deepseek.com → API Keys. Model phổ biến: <code className="text-green-700">deepseek-chat</code> hoặc <code className="text-green-700">deepseek-reasoner</code>.
          </p>
        )}

        <button
          type="button"
          onClick={() => saveLlmMutation.mutate()}
          disabled={saveLlmMutation.isPending}
          className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white hover:bg-slate-50 disabled:opacity-50 px-4 py-2.5 text-xs font-semibold text-slate-700 transition"
        >
          <CheckCircle2 className="w-4 h-4 text-green-600" />
          Lưu cấu hình Script LLM
        </button>
        <p className="text-[11px] text-slate-400">
          Lưu ý: hãy bấm "Lưu cấu hình" trước khi "Test kết nối" — khóa API chỉ được áp dụng sau khi lưu.
        </p>
      </div>
    </div>
  );
}
