import { create } from "zustand";
import type { Scene, User } from "@/lib/types";
import { getStoredToken, setStoredToken } from "@/lib/api";

export interface ToastMessage {
  id: string;
  title: string;
  description?: string;
  variant: "info" | "success" | "warning" | "error";
}

interface ToastState {
  toasts: ToastMessage[];
  push: (toast: Omit<ToastMessage, "id">) => void;
  dismiss: (id: string) => void;
}

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (toast) => {
    const id = `${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    set((s) => ({ toasts: [...s.toasts, { ...toast, id }] }));
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
    }, 4500);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

interface AuthState {
  user: User | null;
  token: string | null;
  setAuth: (token: string, user: User) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: typeof window !== "undefined" ? getStoredToken() : null,
  setAuth: (token, user) => {
    setStoredToken(token);
    set({ token, user });
  },
  logout: () => {
    setStoredToken(null);
    set({ token: null, user: null });
  },
}));

export interface TimelineSnapshot {
  scenes: Scene[];
  config: Record<string, unknown>;
}

interface TimelineEditorState {
  scenes: Scene[];
  config: Record<string, unknown>;
  past: TimelineSnapshot[];
  future: TimelineSnapshot[];
  selectedSceneId: string | null;
  isPlaying: boolean;
  playheadSec: number;
  saveStatus: "idle" | "saving" | "saved" | "error";
  hydrate: (scenes: Scene[], config: Record<string, unknown>) => void;
  commitChange: (scenes: Scene[], config?: Record<string, unknown>) => void;
  undo: () => void;
  redo: () => void;
  setSelectedSceneId: (id: string | null) => void;
  setIsPlaying: (playing: boolean) => void;
  setPlayheadSec: (sec: number) => void;
  setSaveStatus: (status: "idle" | "saving" | "saved" | "error") => void;
}

export const useTimelineStore = create<TimelineEditorState>((set, get) => ({
  scenes: [],
  config: {},
  past: [],
  future: [],
  selectedSceneId: null,
  isPlaying: false,
  playheadSec: 0,
  saveStatus: "idle",
  hydrate: (scenes, config) =>
    set({
      scenes,
      config,
      past: [],
      future: [],
      selectedSceneId: get().selectedSceneId || scenes[0]?.id || null,
      saveStatus: "saved",
    }),
  commitChange: (scenes, config) => {
    const prev = { scenes: get().scenes, config: get().config };
    const nextConfig = config ?? get().config;
    set((s) => ({
      past: [...s.past.slice(-24), prev],
      scenes,
      config: nextConfig,
      future: [],
      saveStatus: "saving",
    }));
  },
  undo: () => {
    const { past, scenes, config, future } = get();
    if (past.length === 0) return;
    const target = past[past.length - 1];
    set({
      past: past.slice(0, -1),
      scenes: target.scenes,
      config: target.config,
      future: [{ scenes, config }, ...future],
      saveStatus: "saving",
    });
  },
  redo: () => {
    const { past, scenes, config, future } = get();
    if (future.length === 0) return;
    const target = future[0];
    set({
      past: [...past, { scenes, config }],
      scenes: target.scenes,
      config: target.config,
      future: future.slice(1),
      saveStatus: "saving",
    });
  },
  setSelectedSceneId: (id) => set({ selectedSceneId: id }),
  setIsPlaying: (isPlaying) => set({ isPlaying }),
  setPlayheadSec: (playheadSec) => set({ playheadSec }),
  setSaveStatus: (saveStatus) => set({ saveStatus }),
}));

