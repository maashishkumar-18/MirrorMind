import { useCallback, useEffect, useRef, useState } from "react";

import { call, IpcCallError } from "../ipc/client";
import { useBackendStore } from "../store/backend";
import { useSessionStore, type SessionMessage } from "../store/session";
import { DisambiguationPopup } from "../ui/DisambiguationPopup";
import {
  formatCitationLabel,
  formatTimestamp,
  isBoundary,
  withBoundaries,
} from "./chatView";
import { S } from "../strings";

function msg(e: unknown): string {
  return e instanceof IpcCallError ? e.message : String(e);
}

const prefersReducedMotion =
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function Chat() {
  const phase = useBackendStore((s) => s.phase);
  const offline = phase === "degraded" || phase === "exited";

  const messages = useSessionStore((s) => s.messages);
  const sending = useSessionStore((s) => s.sending);
  const historyLoading = useSessionStore((s) => s.historyLoading);
  const hydrated = useSessionStore((s) => s.hydrated);
  const pending = useSessionStore((s) => s.pendingDisambiguation);
  const error = useSessionStore((s) => s.error);

  const [draft, setDraft] = useState("");
  const [confirmBusy, setConfirmBusy] = useState(false);

  const bottomRef = useRef<HTMLDivElement>(null);
  const didHydrate = useRef(false);

  const loadHistory = useCallback(() => {
    useSessionStore.getState().startHistoryLoad();
    void call("chat.history", {})
      .then((r) => useSessionStore.getState().hydrate(r))
      .catch((e: unknown) => {
        useSessionStore.getState().failHistoryLoad();
        if (!(e instanceof IpcCallError)) throw e;
      });
  }, []);

  // Mount: pull the current session's transcript. Unmount: a Tier-2 popup never
  // survives leaving /chat (the backend discards its pending action on the next
  // chat.send / chat.new anyway, so this is a visual-only clear).
  useEffect(() => {
    loadHistory();
    return () => useSessionStore.getState().dismissDisambiguation();
  }, [loadHistory]);

  // Auto-scroll on a live turn — but not for the one-shot hydrate() population.
  useEffect(() => {
    if (!didHydrate.current) {
      didHydrate.current = hydrated;
      return;
    }
    bottomRef.current?.scrollIntoView({
      behavior: prefersReducedMotion ? "auto" : "smooth",
    });
  }, [messages.length, sending, hydrated]);

  const send = useCallback((text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    const id = useSessionStore.getState().startSend(trimmed);
    void call("chat.send", { text: trimmed })
      .then((r) => useSessionStore.getState().completeSend(id, r))
      .catch((e) => useSessionStore.getState().failSend(id, msg(e)));
  }, []);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (sending || offline) return;
    send(draft);
    setDraft("");
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (sending || offline) return;
      send(draft);
      setDraft("");
    }
  };

  const retry = (m: SessionMessage) => {
    useSessionStore.getState().removeMessage(m.id);
    send(m.content);
  };

  const newConversation = () => {
    void call("chat.new", {})
      .then((r) => useSessionStore.getState().reset(r.session_id))
      .catch(() => {
        /* client already recorded the error on useBackendStore */
      });
  };

  const resolveDisambiguation = (choice: string) => {
    if (!pending) return;
    setConfirmBusy(true);
    void call("chat.confirm_action", { pending_action_id: pending.pending_action_id, choice })
      .then((r) => useSessionStore.getState().applyConfirmResult(r))
      .catch((e) => {
        useSessionStore.getState().dismissDisambiguation();
        if (e instanceof IpcCallError && e.detail.code === "no_pending_action") {
          useSessionStore.setState({ error: S.chat.promptExpired });
        }
      })
      .finally(() => setConfirmBusy(false));
  };

  const dismissDisambiguation = () => {
    if (!pending) return;
    const id = pending.pending_action_id;
    useSessionStore.getState().dismissDisambiguation();
    setConfirmBusy(true);
    void call("chat.confirm_action", { pending_action_id: id, choice: "conversation" })
      .then((r) => useSessionStore.getState().applyConfirmResult(r))
      .catch(() => {
        /* popup already closed; a stale id is fine */
      })
      .finally(() => setConfirmBusy(false));
  };

  const rows = withBoundaries(messages);

  return (
    <div className="chat-view">
      <header className="chat-header">
        <h1>{S.chat.title}</h1>
        <button type="button" onClick={newConversation} disabled={sending}>
          {S.chat.newConversation}
        </button>
      </header>

      <div className="chat-log">
        {historyLoading ? (
          <p className="chat-log-loading">{S.chat.loadingHistory}</p>
        ) : hydrated && messages.length === 0 ? (
          <p className="chat-empty">
            {S.chat.empty}
          </p>
        ) : !hydrated && !historyLoading ? (
          <p className="chat-log-loading">
            {S.chat.historyError}{" "}
            <button type="button" onClick={loadHistory}>
              {S.loading.retry}
            </button>
          </p>
        ) : (
          rows.map((row) =>
            isBoundary(row) ? (
              <div key={row.id} className="chat-boundary" role="separator">
                <span>{S.chat.newConversation}</span>
              </div>
            ) : (
              <Message key={row.id} m={row} onRetry={retry} />
            ),
          )
        )}

        {sending && (
          <div className="chat-msg chat-typing" data-role="assistant" role="status" aria-live="polite">
            <span>{S.chat.thinking}</span>
            <span className="banner-dots" aria-hidden="true" />
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {error && (
        <p className="chat-offline">
          {error}{" "}
          <button type="button" onClick={() => useSessionStore.getState().clearError()}>
            {S.loading.dismiss}
          </button>
        </p>
      )}
      {offline && (
        <p className="chat-offline">{S.chat.offline}</p>
      )}

      <form className="chat-composer" onSubmit={onSubmit}>
        <textarea
          aria-label={S.chat.messageLabel}
          placeholder={S.chat.messagePlaceholder}
          value={draft}
          rows={2}
          disabled={sending || offline}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button type="submit" aria-label={S.chat.send} disabled={sending || offline || !draft.trim()}>
          {S.chat.sendShort}
        </button>
      </form>

      {pending && (
        <DisambiguationPopup
          options={pending.options}
          busy={confirmBusy}
          onChoose={resolveDisambiguation}
          onDismiss={dismissDisambiguation}
        />
      )}
    </div>
  );
}

function Message({ m, onRetry }: { m: SessionMessage; onRetry: (m: SessionMessage) => void }) {
  return (
    <div className="chat-msg" data-role={m.role} data-status={m.status}>
      <div className="chat-msg-body">{m.content}</div>

      {m.citations.length > 0 && (
        <div className="chat-citations">
          {m.citations.map((c) => (
            <span
              key={c.chunk_id}
              className="chat-citation"
              tabIndex={0}
              title={`${c.chunk_id}\n${c.session_id}\n${c.approximate_timestamp}`}
            >
              {formatCitationLabel(c)}
              <span className="chat-citation-tip" role="tooltip">
                <span>Chunk {c.chunk_id}</span>
                <span>Session {c.session_id}</span>
                <span>≈ {formatTimestamp(c.approximate_timestamp)}</span>
              </span>
            </span>
          ))}
        </div>
      )}

      {m.conflict && (
        <div className="chat-conflict" role="note">
          ⚠️ That overlaps with{" "}
          <strong>{m.conflict.conflicts_with[0]?.title ?? "an existing item"}</strong>
          {m.conflict.conflicts_with[0] &&
            ` (${formatTimestamp(m.conflict.conflicts_with[0].start_time)}–${formatTimestamp(
              m.conflict.conflicts_with[0].end_time,
            )})`}
          . Open the Schedule view to overwrite or keep it.
        </div>
      )}

      {m.createdAt && <time className="chat-msg-time">{formatTimestamp(m.createdAt)}</time>}

      {m.status === "failed" && (
        <button type="button" className="chat-retry" onClick={() => onRetry(m)}>
          {S.loading.retry}
        </button>
      )}
    </div>
  );
}
