import { save } from "@tauri-apps/plugin-dialog";
import { openUrl, revealItemInDir } from "@tauri-apps/plugin-opener";
import { useCallback, useEffect, useState } from "react";

import { call, IpcCallError } from "../ipc/client";
import {
  barWidths,
  formatMs,
  formatPct,
  levelParam,
  LOG_LEVELS,
  reportMailto,
  type LogLevel,
} from "./diagnosticsView";

function msg(e: unknown): string {
  return e instanceof IpcCallError ? e.message : String(e);
}

interface LogEntry {
  timestamp: string;
  level: string;
  logger: string;
  message: string;
}
interface Metrics {
  sample_size: number;
  retrieval_latency_ms: { p50: number; p95: number; p99: number } | null;
  confidence_distribution: { high: number; medium: number; low: number; none: number };
  grounded_rate: number | null;
  retrieval_hit_rate: number | null;
  error_rate: null;
  compute_ms: null;
}

const CONF_LABELS = { high: "High", medium: "Medium", low: "Low", none: "None" } as const;

/**
 * Settings → Diagnostics (Phase 3 Step 3.4): a redacted log viewer, a small
 * local metrics dashboard, and "Report a problem" (save a redacted log, reveal
 * it, open a mailto: compose window).
 */
export function SettingsDiagnostics() {
  const [level, setLevel] = useState<LogLevel>("ALL");
  const [logs, setLogs] = useState<LogEntry[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [logError, setLogError] = useState<string | null>(null);

  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [metricsError, setMetricsError] = useState<string | null>(null);

  const [report, setReport] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const loadLogs = useCallback(() => {
    setLogError(null);
    void call("diagnostics.logs", { level: levelParam(level), limit: 200 })
      .then((r) => {
        setLogs(r.entries);
        setTruncated(r.truncated);
      })
      .catch((e) => setLogError(msg(e)));
  }, [level]);

  useEffect(loadLogs, [loadLogs]);

  useEffect(() => {
    void call("diagnostics.metrics", {})
      .then(setMetrics)
      .catch((e) => setMetricsError(msg(e)));
  }, []);

  const reportProblem = async () => {
    setReport(null);
    let path: string | null;
    try {
      path = await save({ defaultPath: "mirrormind-report.txt" });
    } catch (e) {
      setReport({ kind: "err", text: msg(e) });
      return;
    }
    if (!path) return;
    try {
      const r = await call("diagnostics.report", { path });
      await revealItemInDir(r.path).catch(() => {});
      await openUrl(reportMailto(r.path)).catch(() => {});
      setReport({
        kind: "ok",
        text: `Saved ${r.bytes_written.toLocaleString()} bytes. Attach that file to the email that just opened.`,
      });
    } catch (e) {
      setReport({ kind: "err", text: msg(e) });
    }
  };

  const bars = metrics ? barWidths(metrics.confidence_distribution) : null;

  return (
    <section className="settings-panel">
      <h2>Diagnostics</h2>

      <h3>Recent activity</h3>
      {metricsError ? (
        <p className="first-run-error">{metricsError}</p>
      ) : !metrics ? (
        <p className="settings-hint">Loading…</p>
      ) : metrics.sample_size === 0 ? (
        <p className="settings-hint">No chat activity recorded yet.</p>
      ) : (
        <>
          <p className="settings-hint">Over the last {metrics.sample_size} messages:</p>
          <p className="settings-hint">
            Retrieval latency —{" "}
            {metrics.retrieval_latency_ms
              ? `p50 ${Math.round(metrics.retrieval_latency_ms.p50)} ms · p95 ${Math.round(
                  metrics.retrieval_latency_ms.p95,
                )} ms · p99 ${Math.round(metrics.retrieval_latency_ms.p99)} ms`
              : "not tracked yet"}
          </p>
          <div className="diag-metric-bars">
            {(["high", "medium", "low", "none"] as const).map((k) => (
              <div key={k} className="diag-bar">
                <span>{CONF_LABELS[k]}</span>
                <span className="diag-bar-track">
                  <span className="diag-bar-fill" style={{ width: `${bars![k]}%` }} />
                </span>
                <span>{metrics.confidence_distribution[k]}</span>
              </div>
            ))}
          </div>
          <p className="settings-hint">
            Grounded answers {formatPct(metrics.grounded_rate)} · retrieval hit rate{" "}
            {formatPct(metrics.retrieval_hit_rate)}
          </p>
          <p className="settings-hint">
            Error rate {formatPct(metrics.error_rate)} · compute time {formatMs(metrics.compute_ms)}
          </p>
        </>
      )}

      <h3>Logs</h3>
      <div className="settings-actions">
        <label>
          Level{" "}
          <select value={level} onChange={(e) => setLevel(e.target.value as LogLevel)}>
            {LOG_LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={loadLogs}>
          Refresh
        </button>
      </div>
      {logError && <p className="first-run-error">{logError}</p>}
      {logs && (
        <>
          <div className="diag-log">
            {logs.length === 0 ? (
              <div className="diag-log-line">No entries.</div>
            ) : (
              logs.map((e, i) => (
                <div key={i} className="diag-log-line" data-level={e.level}>
                  <span>{e.timestamp}</span>
                  <span>{e.level}</span>
                  <span>
                    {e.logger} {e.message}
                  </span>
                </div>
              ))
            )}
          </div>
          {truncated && <p className="settings-hint">Showing the last 200 entries.</p>}
        </>
      )}

      <h3>Report a problem</h3>
      <p className="settings-hint">
        Saves a redacted copy of the log (no message content — only events and
        errors), then opens an email so you can attach it and describe the issue.
        Nothing is sent automatically.
      </p>
      <div className="settings-actions">
        <button type="button" onClick={() => void reportProblem()}>
          Report a problem
        </button>
        {report && (
          <span className={report.kind === "ok" ? "settings-ok" : "first-run-error"}>
            {report.text}
          </span>
        )}
      </div>
    </section>
  );
}
