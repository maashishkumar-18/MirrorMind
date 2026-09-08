/**
 * The transient "activate" sub-state of the first-launch flow (fe.6). The
 * download lifecycle lives in `useModelStore` (it must survive navigation);
 * this reducer is only the local activate UI. Pure — unit-tested.
 */
export interface FirstRunState {
  activating: string | null;
  activateError: string | null;
}

export type FirstRunAction =
  | { type: "activate/start"; name: string }
  | { type: "activate/fail"; message: string }
  | { type: "activate/reset" };

export const firstRunInitial: FirstRunState = { activating: null, activateError: null };

export function firstRunReducer(s: FirstRunState, a: FirstRunAction): FirstRunState {
  switch (a.type) {
    case "activate/start":
      return { activating: a.name, activateError: null };
    case "activate/fail":
      return { activating: null, activateError: a.message };
    case "activate/reset":
      return firstRunInitial;
    default:
      return s;
  }
}
