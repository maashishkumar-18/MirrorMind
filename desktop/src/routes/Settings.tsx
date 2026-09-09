import { useSearchParams } from "react-router-dom";

import { SettingsGeneral } from "./SettingsGeneral";
import { SettingsModels } from "./SettingsModels";
import { activeTab, SETTINGS_TAB_LABELS, SETTINGS_TABS, type SettingsTab } from "./settingsView";

/**
 * Settings (Phase 3 Step 3.4). One route; the active panel is a `?tab=` param
 * (deep-linkable, one `<main>`). Data & Privacy / Backup & Recovery / Diagnostics
 * land in 3.4d / 3.4e.
 */
export function Settings() {
  const [params, setParams] = useSearchParams();
  const tab = activeTab(params.get("tab"));

  const select = (next: SettingsTab) => setParams({ tab: next }, { replace: true });

  return (
    <div className="settings-view">
      <h1>Settings</h1>
      <nav className="settings-tabs" aria-label="Settings sections">
        {SETTINGS_TABS.map((t) => (
          <button
            key={t}
            type="button"
            className="settings-tab"
            aria-current={t === tab ? "page" : undefined}
            onClick={() => select(t)}
          >
            {SETTINGS_TAB_LABELS[t]}
          </button>
        ))}
      </nav>

      {tab === "general" && <SettingsGeneral />}
      {tab === "models" && <SettingsModels />}
      {tab === "data" && <ComingSoon name="Data & Privacy" />}
      {tab === "backup" && <ComingSoon name="Backup & Recovery" />}
      {tab === "diagnostics" && <ComingSoon name="Diagnostics" />}
    </div>
  );
}

function ComingSoon({ name }: { name: string }) {
  return (
    <section className="settings-panel">
      <h2>{name}</h2>
      <p className="settings-hint">Coming soon.</p>
    </section>
  );
}
