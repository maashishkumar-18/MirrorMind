// @ts-check
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: ["dist/**", "node_modules/**", "src-tauri/**", "playwright-report/**", "test-results/**"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    languageOptions: {
      globals: { ...globals.browser },
    },
  },
  {
    // The e2e harness runs in Node (Playwright), not the browser.
    files: ["e2e/**/*.{ts,mjs,js}", "playwright.config.ts"],
    languageOptions: {
      globals: { ...globals.node },
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
  {
    // The injected browser shim — browser globals, no TS.
    files: ["e2e/support/tauri-shim.js"],
    languageOptions: {
      globals: { ...globals.browser },
    },
  },
);
