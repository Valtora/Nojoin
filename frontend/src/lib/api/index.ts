// Public API layer barrel. Per the frontend conventions (docs/DEVELOPMENT.md),
// all API communication routes through "@/lib/api". This barrel re-exports the
// typed resource clients so existing import sites stay unchanged while the
// implementation lives in focused per-resource modules. The default export is
// the shared axios instance; only API_BASE_URL is re-exported as a named value
// to match the historical public surface.
import api from "./client";

export { API_BASE_URL } from "./client";

export * from "./auth";
export * from "./recordings";
export * from "./capture";
export * from "./tasks";
export * from "./speakers";
export * from "./voiceprints";
export * from "./transcript";
export * from "./notes";
export * from "./tags";
export * from "./calendar";
export * from "./export";
export * from "./settings";
export * from "./setup";
export * from "./models";
export * from "./system";
export * from "./users";
export * from "./documents";
export * from "./chat";
export * from "./backup";
export * from "./version";

export default api;
