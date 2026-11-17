import type { MediaAsset, User } from "@/lib/types";

const TOKEN_STORAGE_KEY = "ai_video_studio_token";

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setStoredToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } else {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  }
}

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

function formatApiDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (typeof item !== "object" || item === null) return String(item);
      const error = item as { loc?: unknown[]; msg?: unknown };
      const field = Array.isArray(error.loc) ? error.loc.filter((part) => part !== "body").join(" → ") : "Dữ liệu";
      return `${field || "Dữ liệu"}: ${String(error.msg ?? "không hợp lệ")}`;
    }).join("; ");
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return String(detail ?? "Lỗi không xác định");
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getStoredToken();
  const headers = new Headers(init.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (!(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
  });

  if (res.status === 401) {
    setStoredToken(null);
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Session expired. Please sign in again.");
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = { detail: text };
    }
  }

  if (!res.ok) {
    const rawDetail =
      typeof parsed === "object" && parsed && "detail" in parsed
        ? (parsed as { detail: unknown }).detail
        : `Request failed (${res.status})`;
    const detail = formatApiDetail(rawDetail);
    throw new ApiError(res.status, detail);
  }

  return parsed as T;
}

export async function uploadMediaFile(
  file: File,
  assetKind: "image" | "video" | "audio" = "image",
  projectId?: string
): Promise<MediaAsset> {
  const form = new FormData();
  form.append("file", file);
  form.append("asset_kind", assetKind);
  if (projectId) {
    form.append("project_id", projectId);
  }
  return apiFetch<MediaAsset>("/media/upload", {
    method: "POST",
    body: form,
  });
}

export async function loginRequest(
  email: string,
  password: string
): Promise<{ access_token: string; user: User }> {
  return apiFetch<{ access_token: string; user: User }>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function subscribeJobEvents(
  jobId: string,
  onEvent: (payload: {
    event?: string;
    status?: string;
    progress_percent?: number;
    status_message?: string;
    error_code?: string;
    error_message?: string;
  }) => void
): () => void {
  const token = getStoredToken();
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  const es = new EventSource(`/api/v1/jobs/${jobId}/events${qs}`);

  const handle = (ev: MessageEvent) => {
    try {
      const data = JSON.parse(ev.data);
      onEvent(data);
      if (data.status === "completed" || data.status === "failed" || data.status === "cancelled") {
        es.close();
      }
    } catch {
      // ignore non-json heartbeat
    }
  };

  es.addEventListener("snapshot", handle);
  es.addEventListener("running", handle);
  es.addEventListener("progress", handle);
  es.addEventListener("retrying", handle);
  es.addEventListener("completed", handle);
  es.addEventListener("failed", handle);
  es.onmessage = handle;
  es.onerror = () => {
    es.close();
  };

  return () => es.close();
}
