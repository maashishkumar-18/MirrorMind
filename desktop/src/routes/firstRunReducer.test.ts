import { describe, expect, it } from "vitest";

import { firstRunInitial, firstRunReducer } from "./firstRunReducer";

describe("firstRunReducer", () => {
  it("activate/start sets the model and clears a prior error", () => {
    const s = firstRunReducer(
      { activating: null, activateError: "boom" },
      { type: "activate/start", name: "llama3.1:8b" },
    );
    expect(s).toEqual({ activating: "llama3.1:8b", activateError: null });
  });

  it("activate/fail clears activating, keeps the message", () => {
    const s = firstRunReducer(
      { activating: "llama3.1:8b", activateError: null },
      { type: "activate/fail", message: "not installed" },
    );
    expect(s).toEqual({ activating: null, activateError: "not installed" });
  });

  it("activate/reset returns to initial", () => {
    expect(
      firstRunReducer({ activating: "x", activateError: "y" }, { type: "activate/reset" }),
    ).toEqual(firstRunInitial);
  });
});
