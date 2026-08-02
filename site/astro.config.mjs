// @ts-check
import { defineConfig } from "astro/config";
import tailwindcss from "@tailwindcss/vite";
import appTokensMedia from "./plugins/app-tokens-media.mjs";

export default defineConfig({
  site: "https://www.nojoin.co.uk",
  vite: {
    plugins: [appTokensMedia(), tailwindcss()],
    server: {
      fs: {
        // The design tokens are imported from the app by relative path, so the
        // dev server must be allowed to read one level above site/.
        allow: [".."],
      },
    },
    preview: {
      // Lets `npm run serve` be reviewed through a temporary Cloudflare quick
      // tunnel; the preview server only ever serves the public static build.
      allowedHosts: [".trycloudflare.com"],
    },
  },
});
