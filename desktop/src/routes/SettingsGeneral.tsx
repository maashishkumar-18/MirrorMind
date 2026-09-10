import { useEffect, useState } from "react";

import { call, IpcCallError } from "../ipc/client";
import { isValidIdle, isValidTime } from "./settingsView";
import { S } from "../strings";

function msg(e: unknown): string {
  return e instanceof IpcCallError ? e.message : String(e);
}

interface Settings {
  idle_timeout_minutes: number;
  summary_time: string;
  idle_timeout_is_default: boolean;
  summary_time_is_default: boolean;
}

/**
 * Settings → General (Phase 3 Step 3.4). Session idle timeout + daily summary
 * time. The backend returns *effective* values (config → env → YAML → default);
 * `*_is_default` drives the per-field "Use default" affordance. A settings.update
 * takes effect on the next message with no restart.
 */
export function SettingsGeneral() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [idle, setIdle] = useState("");
  const [summary, setSummary] = useState("");
  const [status, setStatus] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  const apply = (s: Settings) => {
    setSettings(s);
    setIdle(String(s.idle_timeout_minutes));
    setSummary(s.summary_time);
  };

  useEffect(() => {
    void call("settings.get", {})
      .then(apply)
      .catch((e) => setStatus({ kind: "err", text: msg(e) }));
  }, []);

  const idleNum = Number(idle);
  const dirty =
    settings != null &&
    (idleNum !== settings.idle_timeout_minutes || summary !== settings.summary_time);
  const valid = isValidIdle(idleNum) && isValidTime(summary);

  const save = (params: { idle_timeout_minutes?: number | null; summary_time?: string | null }) => {
    setSaving(true);
    setStatus(null);
    void call("settings.update", params)
      .then((s) => {
        apply(s);
        setStatus({ kind: "ok", text: S.settings.saved });
      })
      .catch((e) => setStatus({ kind: "err", text: msg(e) }))
      .finally(() => setSaving(false));
  };

  if (!settings) {
    return (
      <section className="settings-panel">
        <h2>{S.settings.general.title}</h2>
        {status?.kind === "err" ? (
          <p className="first-run-error">{status.text}</p>
        ) : (
          <p className="settings-hint">{S.settings.hintLoading}</p>
        )}
      </section>
    );
  }

  return (
    <section className="settings-panel">
      <h2>{S.settings.general.title}</h2>

      <form
        className="settings-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (dirty && valid && !saving) {
            save({ idle_timeout_minutes: idleNum, summary_time: summary });
          }
        }}
      >
        <label className="settings-field">
          <span>{S.settings.general.idleTimeout}</span>
          <input
            type="number"
            min={1}
            max={1440}
            value={idle}
            onChange={(e) => setIdle(e.target.value)}
          />
          {settings.idle_timeout_is_default ? (
            <em className="settings-default">using the default (45)</em>
          ) : (
            <button
              type="button"
              className="settings-reset"
              disabled={saving}
              onClick={() => save({ idle_timeout_minutes: null })}
            >
              {S.settings.useDefault}
            </button>
          )}
        </label>

        <label className="settings-field">
          <span>{S.settings.general.dailySummaryTime}</span>
          <input type="time" value={summary} onChange={(e) => setSummary(e.target.value)} />
          {settings.summary_time_is_default ? (
            <em className="settings-default">using the default (21:00)</em>
          ) : (
            <button
              type="button"
              className="settings-reset"
              disabled={saving}
              onClick={() => save({ summary_time: null })}
            >
              {S.settings.useDefault}
            </button>
          )}
        </label>

        <div className="settings-actions">
          <button type="submit" disabled={!dirty || !valid || saving}>
            {saving ? S.settings.saving : S.settings.save}
          </button>
          {dirty && !valid && (
            <span className="first-run-error">
              Minutes must be 1–1440; time must be HH:MM.
            </span>
          )}
          {status && (
            <span className={status.kind === "ok" ? "settings-ok" : "first-run-error"}>
              {status.text}
            </span>
          )}
        </div>
      </form>
    </section>
  );
}
