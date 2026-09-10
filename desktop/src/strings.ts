/**
 * Centralised user-facing copy (Phase 4 Step 4.5 — string externalization).
 *
 * MirrorMind is a single-user, English-only, local app (project_logic.md §1–2),
 * so this is not an i18n layer — there is no locale switch and no framework.
 * It is a single review surface for the strings a screen-reader user hears and
 * the copy that would need attention if localization is ever added: view
 * titles, empty / loading / error states, `aria-label`s, live-region messages,
 * and input placeholders.
 *
 * Backend-authoritative copy stays in Python and arrives over IPC —
 * `data.info` strings (`NEVER_EXPORTED_LINE` / `UNINSTALL_WARNING` /
 * `EXPORT_BLURB` in `src/features/data_admin.py`), the degraded-banner /
 * app-gate wording in `ui/bannerState.ts` / `ui/gateState.ts` (already pure
 * modules), and every assistant message.
 */

export const S = {
  app: {
    name: "MirrorMind",
    starting: "Starting MirrorMind…",
  },

  nav: {
    primary: "Primary",
    settingsSections: "Settings sections",
    exportBadge: "Data export recommended",
  },

  loading: {
    dismiss: "Dismiss",
    retry: "Retry",
  },

  firstRun: {
    recoveryTitle: "Recovery mode",
    recoveryBody: "MirrorMind needs a backup restored before you can set up a model.",
    chooseTitle: "Choose a model",
    chooseBody: "Pick a model to download. You can add or switch models later in Settings.",
    loadingModels: "Loading models…",
    servicePaused: "The model service isn’t running — downloads are paused.",
    catalogError: "Couldn’t load the model list",
    recommended: "Recommended",
    ramSuffix: "GB RAM",
    download: "Download",
    downloadStarting: "starting…",
    activate: "Activate",
    activating: "Activating…",
    switch: "Switch",
    currentlyActive: "Currently active",
    downloaded: (name: string) => `Downloaded ${name}`,
    integrityVerified: "· integrity verified",
    integrityUnverified: "· couldn’t verify — try re-downloading",
  },

  chat: {
    title: "Chat",
    newConversation: "New conversation",
    messageLabel: "Message",
    messagePlaceholder: "Ask MirrorMind anything…",
    send: "Send message",
    sendShort: "Send",
    promptExpired: "That prompt expired — send your message again.",
    loadingHistory: "Loading your conversation…",
    empty: "Start a conversation — ask about anything you’ve talked about before.",
    historyError: "Couldn’t load your conversation.",
    thinking: "MirrorMind is thinking",
    offline: "Chat is unavailable while MirrorMind recovers.",
  },

  disambiguation: {
    label: "Confirm what you meant",
    prompt: "What would you like me to do?",
    dismiss: "Dismiss",
  },

  features: {
    empty: "Nothing here yet.",
    loading: "Loading…",
    didYouMean: "Did you mean:",
    conflictHint: "— open the Schedule view to resolve it.",
    dismiss: "Dismiss",
    /** aria-label for a row's complete checkbox: `complete("Water the plants")` */
    complete: (title: string) => `Complete ${title}`,
    reschedule: (title: string) => `Reschedule ${title}`,
    reminders: {
      title: "Reminders",
      createLabel: "New reminder",
      createPlaceholder: "Remind me to call John on Tuesday at 2pm",
      add: "Add",
      groups: { overdue: "Overdue", today: "Today", upcoming: "Upcoming" },
      needsAck: "needs acknowledgment",
      loadError: "Couldn’t load reminders",
    },
    todos: {
      title: "To-dos",
      createLabel: "New to-do",
      createPlaceholder: "Add a todo: draft the Q3 report, high priority",
      noPriority: "No priority",
      completed: (n: number) => `Completed (${n})`,
    },
    meetings: {
      title: "Meetings",
      transcriptLabel: "Meeting transcript",
      transcriptPlaceholder: "Paste or type a transcript, then Capture…",
      capture: "Capture",
      capturing: "Capturing…",
      reviewNeeded: "Review needed",
      transcript: "Transcript",
      detail: {
        attendees: "Attendees",
        topics: "Topics",
        decisions: "Decisions",
        actionItems: "Action items",
        followUps: "Follow-ups",
      },
    },
    schedule: {
      title: "Schedule",
      editLabel: "Natural-language schedule edit",
      editPlaceholder: "Schedule a review with Sam 3-4pm Thursday",
      empty: "Nothing scheduled.",
      day: "Day",
      week: "Week",
      prev: "Previous",
      next: "Next",
      today: "Today",
      apply: "Apply",
      overwrite: "Overwrite",
      keepExisting: "Keep existing",
      loadError: "Couldn’t load the schedule",
      weekOf: (label: string) => `Week of ${label}`,
    },
  },

  settings: {
    title: "Settings",
    hintLoading: "Loading…",
    saved: "Saved.",
    useDefault: "Use default",
    save: "Save",
    saving: "Saving…",
    general: {
      title: "General",
      idleTimeout: "Close a conversation after (minutes of no messages)",
      dailySummaryTime: "Daily summary time",
    },
    models: { title: "Models" },
    backup: {
      title: "Backup & Recovery",
      restarting: "Restarting to apply this backup…",
      noSnapshots: "No snapshots yet.",
      restoreTitle: "Restore from this backup?",
      restoreConfirm: "Restore and restart",
    },
    data: {
      title: "Data & Privacy",
      export: "Export all data",
      exporting: "Exporting…",
      wipeTitle: "Delete all your data?",
      wipeConfirm: "Delete everything",
    },
    diagnostics: {
      title: "Diagnostics",
      recentActivity: "Recent activity",
      logs: "Logs",
      noActivity: "No chat activity recorded yet.",
      noEntries: "No entries.",
      reportProblem: "Report a problem",
    },
  },
} as const;
