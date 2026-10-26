// Public API layer barrel. Per the frontend conventions (docs/DEVELOPMENT.md),
// all API communication routes through "@/lib/api", which re-exports the typed
// per-resource clients. The default export is the shared axios instance;
// API_BASE_URL is the only named value re-exported from the client.
import api from "./client";

export { API_BASE_URL } from "./client";

export * from "./auth";
export * from "./recordings";
export * from "./capture";
export * from "./speakerFrames";
export * from "./tasks";
export * from "./speakers";
export * from "./voiceprints";
export * from "./transcript";
export * from "./notes";
export * from "./notesTemplates";
export * from "./tags";
export * from "./calendar";
export * from "./export";
export * from "./settings";
export * from "./setup";
export * from "./models";
export * from "./oauth";
export * from "./system";
export * from "./users";
export * from "./documents";
export * from "./chat";
export * from "./analytics";
export * from "./backup";
export * from "./version";

export default api;
