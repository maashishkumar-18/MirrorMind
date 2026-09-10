import { useEffect, useRef } from "react";

import { disambigLabel } from "../routes/chatView";
import { S } from "../strings";

/**
 * Tier-2 disambiguation (project_logic §3): a small card over the transcript —
 * not a full-screen modal. One button per backend-supplied option; × and the
 * backdrop both dismiss (which the caller turns into a `chat.confirm_action`
 * with choice "conversation").
 */
export function DisambiguationPopup({
  options,
  busy,
  onChoose,
  onDismiss,
}: {
  options: string[];
  busy: boolean;
  onChoose: (choice: string) => void;
  onDismiss: () => void;
}) {
  const firstRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    firstRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onDismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onDismiss]);

  return (
    <div className="disambig-backdrop" onClick={onDismiss}>
      <div
        className="disambig-card"
        role="dialog"
        aria-modal="true"
        aria-label={S.disambiguation.label}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="disambig-head">
          <span>{S.disambiguation.prompt}</span>
          <button
            type="button"
            className="disambig-close"
            aria-label={S.disambiguation.dismiss}
            onClick={onDismiss}
          >
            ×
          </button>
        </div>
        <ul className="disambig-options">
          {options.map((opt, i) => (
            <li key={opt}>
              <button
                type="button"
                ref={i === 0 ? firstRef : undefined}
                disabled={busy}
                onClick={() => onChoose(opt)}
              >
                {disambigLabel(opt)}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
