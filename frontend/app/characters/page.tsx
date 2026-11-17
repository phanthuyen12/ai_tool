"use client";

import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Users, Plus, Upload, Trash2, ShieldCheck, Check } from "lucide-react";
import { apiFetch, uploadMediaFile } from "@/lib/api";
import { useToastStore } from "@/lib/store";
import type { Character, MediaAsset } from "@/lib/types";

export default function CharactersPage() {
  const qc = useQueryClient();
  const pushToast = useToastStore((s) => s.push);

  const [name, setName] = useState("");
  const [roleTitle, setRoleTitle] = useState("Presenter");
  const [ageAppearance, setAgeAppearance] = useState("45");
  const [voiceStyle, setVoiceStyle] = useState("Mature American male, calm authoritative tone");
  const [screenTime, setScreenTime] = useState(40);
  const [clothingRule, setClothingRule] = useState("Preserve original face and clothing");
  const [preserveFace, setPreserveFace] = useState(true);
  const [staticCamera, setStaticCamera] = useState(true);
  const [noAutoBgm, setNoAutoBgm] = useState(true);
  const [noExtraChars, setNoExtraChars] = useState(true);
  const [uploadedAsset, setUploadedAsset] = useState<MediaAsset | null>(null);
  const [importUrl, setImportUrl] = useState("");
  const [uploading, setUploading] = useState(false);

  const charsQuery = useQuery({
    queryKey: ["characters"],
    queryFn: () => apiFetch<Character[]>("/characters"),
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      let assetId = uploadedAsset?.id;
      if (!assetId && importUrl.trim()) {
        const imported = await apiFetch<MediaAsset>("/media/import-url", {
          method: "POST",
          body: JSON.stringify({ url: importUrl.trim(), asset_kind: "image" }),
        });
        assetId = imported.id;
      }
      return apiFetch<Character>("/characters", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          role_title: roleTitle,
          age_appearance: ageAppearance,
          voice_style: voiceStyle,
          default_screen_time_seconds: screenTime,
          primary_image_asset_id: assetId || null,
          reference_asset_ids: assetId ? [assetId] : [],
          rule: {
            preserve_face: preserveFace,
            preserve_hair_and_age: true,
            clothing_rule: clothingRule,
            preserve_skin_and_identity: true,
            natural_movement_only: true,
            no_extra_characters: noExtraChars,
            preserve_visual_style: true,
            hand_gesture_level: "moderate",
            static_camera_on_speech: staticCamera,
            no_auto_bgm: noAutoBgm,
            custom_directives: "",
          },
        }),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["characters"] });
      setName("");
      setImportUrl("");
      setUploadedAsset(null);
      pushToast({ title: "Đã thêm nhân vật mới", variant: "success" });
    },
    onError: (err: unknown) => {
      pushToast({
        title: "Không thể tạo nhân vật",
        description: err instanceof Error ? err.message : "Lỗi không xác định",
        variant: "error",
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/characters/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["characters"] });
      pushToast({ title: "Đã xóa nhân vật", variant: "info" });
    },
  });

  async function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setUploading(true);
    try {
      const asset = await uploadMediaFile(f, "image");
      setUploadedAsset(asset);
      pushToast({ title: "Đã tải ảnh nhân vật lên", variant: "success" });
    } catch (err: unknown) {
      pushToast({
        title: "Lỗi upload ảnh",
        description: err instanceof Error ? err.message : "Upload thất bại",
        variant: "error",
      });
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="p-6 lg:p-8 max-w-7xl w-full mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2.5">
          <Users className="w-6 h-6 text-green-600" />
          Thư viện Nhân vật & Quy tắc Đồng nhất (Character Rules)
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          Tải ảnh chân dung nhận diện, cấu hình giọng nói thuyết minh và khóa quy tắc khuôn mặt/trang phục cho toàn chiến dịch.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Create Character Form */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!name.trim()) return;
            createMutation.mutate();
          }}
          className="rounded-2xl border border-slate-200 bg-white p-5 space-y-4 h-fit shadow-sm"
        >
          <h2 className="text-sm font-semibold text-slate-900 flex items-center gap-2">
            <Plus className="w-4 h-4 text-green-600" />
            Import Nhân vật mới
          </h2>

          <div>
            <div className="mb-1 flex items-center justify-between"><label className="block text-xs text-slate-500">Tên nhân vật *</label><span className={`text-xs ${name.length >= 190 ? "text-amber-700" : "text-slate-400"}`}>{name.length}/200</span></div>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="VD: Senior Forex Expert"
              maxLength={200}
              required
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 focus:border-green-500 focus:ring-2 focus:ring-green-500/15 focus:outline-none"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Vai trò</label>
              <input
                value={roleTitle}
                onChange={(e) => setRoleTitle(e.target.value)}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Độ tuổi hiển thị</label>
              <input
                value={ageAppearance}
                onChange={(e) => setAgeAppearance(e.target.value)}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs text-slate-500 mb-1">Mô tả giọng nói (cho Omni 1.2)</label>
            <input
              value={voiceStyle}
              onChange={(e) => setVoiceStyle(e.target.value)}
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Thời lượng mặc định (giây)</label>
              <input
                type="number"
                min={4}
                max={600}
                value={screenTime}
                onChange={(e) => setScreenTime(Number(e.target.value))}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Quy tắc trang phục</label>
              <input
                value={clothingRule}
                onChange={(e) => setClothingRule(e.target.value)}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
              />
            </div>
          </div>

          {/* Upload or Cloudinary URL */}
          <div className="space-y-2 pt-1">
            <label className="block text-xs text-slate-500">Ảnh nhận diện chính (Upload hoặc Cloudinary URL)</label>
            <label className="flex items-center justify-center gap-2 rounded-xl border border-dashed border-slate-300 hover:border-green-500 bg-slate-50 p-3 text-xs text-slate-600 cursor-pointer transition">
              <Upload className="w-4 h-4 text-green-600" />
              {uploading
                ? "Đang tải lên..."
                : uploadedAsset
                ? `Đã chọn: ${uploadedAsset.original_filename}`
                : "Chọn file ảnh từ máy (JPG/PNG/WebP)"}
              <input type="file" accept="image/*" onChange={handleFileSelect} className="hidden" />
            </label>
            <input
              value={importUrl}
              onChange={(e) => setImportUrl(e.target.value)}
              placeholder="Hoặc dán https://res.cloudinary.com/..."
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
            />
          </div>

          {/* Character Rules Toggles */}
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 space-y-2">
            <div className="text-xs font-medium text-green-700 flex items-center gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5" />
              Character Consistency Rules
            </div>
            <RuleCheck checked={preserveFace} onChange={setPreserveFace} label="Giữ nguyên khuôn mặt & màu da gốc" />
            <RuleCheck checked={staticCamera} onChange={setStaticCamera} label="Khóa camera tĩnh khi nhân vật thuyết minh" />
            <RuleCheck checked={noExtraChars} onChange={setNoExtraChars} label="Cấm tự ý sinh thêm người phụ" />
            <RuleCheck checked={noAutoBgm} onChange={setNoAutoBgm} label="Không tự chèn nhạc nền AI trong clip" />
          </div>

          <button
            type="submit"
            disabled={createMutation.isPending}
            className="w-full rounded-xl bg-green-600 hover:bg-green-700 disabled:opacity-50 py-2.5 text-xs font-semibold text-white shadow-sm shadow-green-600/20 transition"
          >
            {createMutation.isPending ? "Đang lưu..." : "Lưu Nhân vật vào Thư viện"}
          </button>
        </form>

        {/* Character Cards Grid */}
        <div className="lg:col-span-2 grid grid-cols-1 md:grid-cols-2 gap-4 h-fit">
          {(charsQuery.data || []).length === 0 ? (
            <div className="col-span-full rounded-2xl border border-dashed border-slate-200 p-12 text-center text-sm text-slate-500">
              Chưa có nhân vật nào trong thư viện.
            </div>
          ) : (
            (charsQuery.data || []).map((c) => (
              <div
                key={c.id}
                className="rounded-2xl border border-slate-200 bg-white overflow-hidden flex flex-col justify-between shadow-sm"
              >
                <div className="p-4 flex gap-4">
                  <div className="w-20 h-24 rounded-xl bg-slate-100 border border-slate-200 overflow-hidden shrink-0 flex items-center justify-center">
                    {c.primary_image_url ? (
                      <img src={c.primary_image_url} alt={c.name} className="w-full h-full object-cover" />
                    ) : (
                      <Users className="w-7 h-7 text-slate-300" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="text-sm font-bold text-slate-900 truncate">{c.name}</h3>
                      <button
                        onClick={() => deleteMutation.mutate(c.id)}
                        className="text-slate-400 hover:text-rose-500 p-1"
                        title="Xóa nhân vật"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                    <p className="text-xs text-green-700">
                      {c.role_title} {c.age_appearance ? `• ~${c.age_appearance} tuổi` : ""}
                    </p>
                    <p className="text-[11px] text-slate-500 line-clamp-2">Giọng: {c.voice_style}</p>
                    <p className="text-[11px] text-slate-400">
                      Mặc định: {c.default_screen_time_seconds}s • Đang dùng ở {c.project_usage_count} dự án
                    </p>
                  </div>
                </div>
                {c.rule && (
                  <div className="px-4 py-2.5 bg-slate-50 border-t border-slate-200 flex flex-wrap gap-1.5 text-[10px] text-slate-600">
                    {c.rule.preserve_face && <span className="px-2 py-0.5 rounded bg-slate-100">Face Lock</span>}
                    {c.rule.static_camera_on_speech && (
                      <span className="px-2 py-0.5 rounded bg-slate-100">Static Cam</span>
                    )}
                    {c.rule.no_auto_bgm && <span className="px-2 py-0.5 rounded bg-slate-100">No Auto BGM</span>}
                    <span className="px-2 py-0.5 rounded bg-slate-100 truncate max-w-[180px]">
                      {c.rule.clothing_rule}
                    </span>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function RuleCheck({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="w-full flex items-center gap-2 text-left text-xs text-slate-600 hover:text-slate-900"
    >
      <span
        className={`w-4 h-4 rounded flex items-center justify-center border ${
          checked ? "bg-green-600 border-green-600 text-white" : "border-slate-300 bg-white"
        }`}
      >
        {checked && <Check className="w-3 h-3" />}
      </span>
      <span>{label}</span>
    </button>
  );
}
