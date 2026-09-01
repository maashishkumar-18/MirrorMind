// @ts-check
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    ignores: ["node_modules/**", "dist/**"],
  },
  {
    rules: {
      // The test suite uses `const { key, ...rest } = obj` to build "envelope
      // minus one field" fixtures — the destructured `key` is intentionally
      // unused, only `...rest` matters. ignoreRestSiblings is the standard
      // fix for exactly this idiom (see typescript-eslint docs).
      "@typescript-eslint/no-unused-vars": ["error", { ignoreRestSiblings: true }],
    },
  }
);
