import type { MeetingNoteWire } from "@ipc/methods";
import { create } from "zustand";
import type { z } from "zod";

export type MeetingNote = z.infer<typeof MeetingNoteWire>;

/**
 * Meetings state (Phase 3 Step 3.3d). Capture goes through the dedicated
 * `meetings.capture` method (Q1) — no conversational response — and the new
 * note is prepended so it lands at the top of the list.
 */
export interface MeetingState {
  meetings: MeetingNote[];
  loading: boolean;
  error: string | null;
  capturing: boolean;

  startLoad: () => void;
  setMeetings: (meetings: MeetingNote[]) => void;
  failLoad: (message: string) => void;
  startCapture: () => void;
  captureDone: () => void;
  prependMeeting: (note: MeetingNote) => void;
  removeMeeting: (id: string) => void;
  clearError: () => void;
}

export const useMeetingStore = create<MeetingState>((set) => ({
  meetings: [],
  loading: false,
  error: null,
  capturing: false,

  startLoad: () => set({ loading: true, error: null }),
  setMeetings: (meetings) => set({ meetings, loading: false, error: null }),
  failLoad: (message) => set({ loading: false, error: message }),
  startCapture: () => set({ capturing: true, error: null }),
  captureDone: () => set({ capturing: false }),
  prependMeeting: (note) => set((s) => ({ meetings: [note, ...s.meetings] })),
  removeMeeting: (id) => set((s) => ({ meetings: s.meetings.filter((m) => m.id !== id) })),
  clearError: () => set({ error: null }),
}));
