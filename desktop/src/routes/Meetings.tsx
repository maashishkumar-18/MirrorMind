import { useCallback, useEffect, useState } from "react";

import { call, IpcCallError } from "../ipc/client";
import { useMeetingStore, type MeetingNote } from "../store/meetings";
import { actionItemLine, formatMeetingDate, summarizeMeeting } from "./meetingsView";

function errText(e: unknown): string {
  return e instanceof IpcCallError ? e.message : String(e);
}

export function Meetings() {
  const meetings = useMeetingStore((s) => s.meetings);
  const loading = useMeetingStore((s) => s.loading);
  const error = useMeetingStore((s) => s.error);
  const capturing = useMeetingStore((s) => s.capturing);

  const [transcript, setTranscript] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const refresh = useCallback(() => {
    useMeetingStore.getState().startLoad();
    void call("meetings.list", {})
      .then((r) => useMeetingStore.getState().setMeetings(r.meetings))
      .catch((e: unknown) => {
        useMeetingStore.getState().failLoad(errText(e));
        if (!(e instanceof IpcCallError)) throw e;
      });
  }, []);

  useEffect(refresh, [refresh]);

  const capture = (e: React.FormEvent) => {
    e.preventDefault();
    const text = transcript.trim();
    if (!text || capturing) return;
    useMeetingStore.getState().startCapture();
    void call("meetings.capture", { transcript: text })
      .then((r) => {
        useMeetingStore.getState().prependMeeting(r.meeting);
        setTranscript("");
        setExpandedId(r.meeting.id);
      })
      .catch((e: unknown) => useMeetingStore.getState().failLoad(errText(e)))
      .finally(() => useMeetingStore.getState().captureDone());
  };

  const remove = (m: MeetingNote) =>
    void call("meetings.delete", { id: m.id })
      .then(() => useMeetingStore.getState().removeMeeting(m.id))
      .catch(() => {});

  return (
    <div className="feature-view">
      <header className="feature-header">
        <h1>Meetings</h1>
      </header>

      <form className="meeting-capture" onSubmit={capture}>
        <textarea
          aria-label="Meeting transcript"
          placeholder="Paste or type a transcript, then Capture…"
          rows={4}
          value={transcript}
          disabled={capturing}
          onChange={(e) => setTranscript(e.target.value)}
        />
        <button type="submit" disabled={capturing || !transcript.trim()}>
          {capturing ? "Capturing…" : "Capture"}
        </button>
      </form>

      {error && <p className="feature-error">{error}</p>}
      {loading && meetings.length === 0 && <p className="feature-loading">Loading…</p>}
      {!loading && meetings.length === 0 && !error && (
        <p className="feature-empty">Nothing here yet.</p>
      )}

      <ul className="feature-list">
        {meetings.map((m) => {
          const open = expandedId === m.id;
          return (
            <li key={m.id} className="feature-row meeting-row">
              <div className="feature-row-body">
                <button
                  type="button"
                  className="meeting-toggle"
                  aria-expanded={open}
                  onClick={() => setExpandedId(open ? null : m.id)}
                >
                  <span className="feature-row-title">{summarizeMeeting(m)}</span>
                  <span className="feature-row-meta">
                    {formatMeetingDate(m.created_at)}
                    {m.needs_review && <span className="needs-review-badge">Review needed</span>}
                  </span>
                </button>

                {open && (
                  <div className="meeting-detail">
                    <Detail label="Attendees" items={m.attendees} />
                    <Detail label="Topics" items={m.topics} />
                    <Detail label="Decisions" items={m.decisions} />
                    <Detail
                      label="Action items"
                      items={m.action_items.map(actionItemLine)}
                    />
                    <Detail label="Follow-ups" items={m.follow_ups} />
                    {m.raw_transcript && (
                      <details className="meeting-transcript">
                        <summary>Transcript</summary>
                        <pre>{m.raw_transcript}</pre>
                      </details>
                    )}
                  </div>
                )}
              </div>
              <button type="button" className="feature-delete" onClick={() => remove(m)}>
                Delete
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Detail({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="meeting-detail-section">
      <h3>{label}</h3>
      <ul>
        {items.map((it, i) => (
          <li key={`${label}-${i}`}>{it}</li>
        ))}
      </ul>
    </div>
  );
}
