/**
 * ESLint flat config.
 *
 * `package.json` has carried a `lint` script since the repo was created, but no
 * eslint and no config — so `npm run lint` has always exited "eslint: not
 * found". A quality gate that has never run is worse than none: it reads as
 * covered in CI and in review while catching nothing.
 *
 * `eslint-config-next` 16 ships flat config natively, so these are imported
 * directly rather than through `FlatCompat` — the compat shim re-serialises the
 * config and chokes on the plugin's own circular references.
 *
 * Type checking stays with `tsc --noEmit`, which is what `make lint` already
 * runs. This layer is for the things types cannot see: hook dependencies,
 * unreachable branches, and Next's own image and navigation rules.
 */

import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const config = [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "next-env.d.ts",
      // Written by `make types` from backend/schema.yml and never hand-edited
      // (§8.2, defect H2). Linting a generated file only invites someone to
      // "fix" it in place, which is the drift the generation exists to stop.
      "src/lib/types.generated.ts",
    ],
  },
  ...coreWebVitals,
  ...typescript,
];

export default config;
