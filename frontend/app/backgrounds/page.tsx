"use client";

import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Image as ImageIcon, Plus, Upload, Trash2, Check, Lock } from "lucide-react";
import { apiFetch, uploadMediaFile } from "@/lib/api";
import { useToastStore } from "@/lib/store";
import type { Background, MediaAsset } from "@/lib/types";

export default function BackgroundsPage() {
  const qc = useQueryClient();
  const pushToast = useToastStore((s) => s.push);

  const [name, setName] = useState("");
  const [style, setStyle] = useState("photorealistic");
  const [description, setDescription] = useState("");
  const [cloudinaryUrl, setCloudinaryUrl] = useState("");
  const [uploadedAsset, setUploadedAsset] = useState<MediaAsset | null>(null);
  const [uploading, setUploading] = useState(false);

  const [keepDesk, setKeepDesk] = useState(true);
  const [keepCam, setKeepCam] = useState(true);
  const [keepMonitors, setKeepMonitors] = useState(true);
  const [keepLighting, setKeepLighting] = useState(true);
  const [seatProperly, setSeatProperly] = useState(true);
  const [customRules, setCustomRules] = useState("");

  const bgsQuery = useQuery({
    queryKey: ["backgrounds"],
    queryFn: () => apiFetch<Background[]>("/backgrounds"),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      apiFetch<Background>("/backgrounds", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          description,
          style,
          media_asset_id: uploadedAsset?.id || null,
          cloudinary_url: cloudinaryUrl.trim() || null,
          preservation_rules_json: {
            keep_desk_position: keepDesk,
            keep_camera_angle: keepCam,
            keep_monitors: keepMonitors,
            keep_lighting: keepLighting,
            keep_room_layout: true,
            seat_character_properly: seatProperly,
            no_extra_objects: true,
            custom_rules: customRules,
          },
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["backgrounds"] });
      setName("");
      setDescription("");
      setCloudinaryUrl("");
      setUploadedAsset(null);
      pushToast({ title: "Đã thêm Background mới", variant: "success" });
    },
    onError: (err: unknown) => {
      pushToast({
        title: "Lỗi tạo Background",
        description: err instanceof Error ? err.message : "Không thể lưu",
        variant: "error",
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/backgrounds/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["backgrounds"] });
      pushToast({ title: "Đã xóa Background", variant: "info" });
    },
  });

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const asset = await uploadMediaFile(file, "image");
      setUploadedAsset(asset);
      pushToast({ title: "Đã tải ảnh nền lên", variant: "success" });
    } catch (err: unknown) {
      pushToast({
        title: "Upload thất bại",
        description: err instanceof Error ? err.message : "Lỗi tải file",
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
          <ImageIcon className="w-6 h-6 text-green-600" />
          Thư viện Background & Quy tắc Bảo toàn Bối cảnh
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          Giữ nguyên bàn làm việc, màn hình chart, góc máy và ánh sáng xuyên suốt tất cả các phân cảnh.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
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
            Thêm Background mới
          </h2>

          <div>
            <div className="mb-1 flex items-center justify-between"><label className="block text-xs text-slate-500">Tên bối cảnh *</label><span className={`text-xs ${name.length >= 190 ? "text-amber-700" : "text-slate-400"}`}>{name.length}/200</span></div>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="VD: Modern Forex Trading Office"
              maxLength={200}
              required
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Phong cách</label>
              <select
                value={style}
                onChange={(e) => setStyle(e.target.value)}
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
              >
                <option value="photorealistic">Photorealistic</option>
                <option value="cinematic">Cinematic Studio</option>
                <option value="newsroom">Broadcast Newsroom</option>
                <option value="minimalist">Minimalist Office</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Mô tả ngắn</label>
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Bàn gỗ tối, 3 màn hình..."
                className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
              />
            </div>
          </div>

          <div className="space-y-2">
            <label className="block text-xs text-slate-500">Ảnh gốc (Upload hoặc Cloudinary URL)</label>
            <label className="flex items-center justify-center gap-2 rounded-xl border border-dashed border-slate-300 hover:border-green-500 bg-slate-50 p-3 text-xs text-slate-600 cursor-pointer transition">
              <Upload className="w-4 h-4 text-green-600" />
              {uploading
                ? "Đang tải lên..."
                : uploadedAsset
                ? `Đã tải: ${uploadedAsset.original_filename}`
                : "Chọn ảnh nền từ máy"}
              <input type="file" accept="image/*" onChange={handleUpload} className="hidden" />
            </label>
            <input
              value={cloudinaryUrl}
              onChange={(e) => setCloudinaryUrl(e.target.value)}
              placeholder="Hoặc dán Cloudinary URL..."
              className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-900"
            />
          </div>

          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 space-y-2">
            <div className="text-xs font-medium text-green-700 flex items-center gap-1.5">
              <Lock className="w-3.5 h-3.5" />
              Quy tắc bảo toàn bối cảnh
            </div>
            <ToggleRow checked={keepDesk} onChange={setKeepDesk} label="Giữ nguyên vị trí bàn làm việc" />
            <ToggleRow checked={keepCam} onChange={setKeepCam} label="Giữ nguyên góc máy camera" />
            <ToggleRow checked={keepMonitors} onChange={setKeepMonitors} label="Giữ nguyên các màn hình phía sau" />
            <ToggleRow checked={keepLighting} onChange={setKeepLighting} label="Giữ nguyên ánh sáng căn phòng" />
            <ToggleRow checked={seatProperly} onChange={setSeatProperly} label="Nhân vật ngồi đúng vị trí ghế" />
            <input
              value={customRules}
              onChange={(e) => setCustomRules(e.target.value)}
              placeholder="Quy tắc bổ sung tùy chỉnh..."
              className="w-full rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-900 mt-1"
            />
          </div>

          <button
            type="submit"
            disabled={createMutation.isPending}
            className="w-full rounded-xl bg-green-600 hover:bg-green-700 disabled:opacity-50 py-2.5 text-xs font-semibold text-white shadow-sm shadow-green-600/20 transition"
          >
            {createMutation.isPending ? "Đang lưu..." : "Lưu Background"}
          </button>
        </form>

        <div className="lg:col-span-2 grid grid-cols-1 md:grid-cols-2 gap-4 h-fit">
          {(bgsQuery.data || []).length === 0 ? (
            <div className="col-span-full rounded-2xl border border-dashed border-slate-200 p-12 text-center text-sm text-slate-500">
              Chưa có ảnh nền nào trong thư viện.
            </div>
          ) : (
            (bgsQuery.data || []).map((bg) => (
              <div
                key={bg.id}
                className="rounded-2xl border border-slate-200 bg-white overflow-hidden flex flex-col shadow-sm"
              >
                <div className="h-40 bg-slate-100 relative flex items-center justify-center overflow-hidden">
                  {bg.image_url ? (
                    <img src={bg.image_url} alt={bg.name} className="w-full h-full object-cover" />
                  ) : (
                    <ImageIcon className="w-8 h-8 text-slate-300" />
                  )}
                  <span className="absolute top-2.5 left-2.5 px-2 py-0.5 rounded-md bg-black/70 backdrop-blur text-[10px] uppercase font-mono text-white">
                    {bg.style}
                  </span>
                </div>
                <div className="p-4 flex-1 flex flex-col justify-between gap-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <h3 className="text-sm font-bold text-slate-900">{bg.name}</h3>
                      <p className="text-xs text-slate-500 mt-0.5">
                        {bg.description || "Bảo toàn bố cục phòng, bàn ghế và ánh sáng."}
                      </p>
                    </div>
                    <button
                      onClick={() => deleteMutation.mutate(bg.id)}
                      className="text-slate-400 hover:text-rose-500 p-1"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                  <div className="text-[11px] text-slate-400">Đang dùng ở {bg.project_usage_count} dự án</div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function ToggleRow({
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
