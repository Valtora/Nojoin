// The public demo is this frontend built with NEXT_PUBLIC_DEMO_MODE=true and
// served against the fixture API in demo/. The flag is inlined at build time,
// so a normal build compiles every demo branch away and no response from a
// server can switch a real install into demo mode.
export const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === "true";

// The fixture API accepts any credentials; these are what the one-click entry
// sends.
export const DEMO_USERNAME = "demo";
export const DEMO_PASSWORD = "demo";

export const DEMO_GET_NOJOIN_URL = "https://github.com/Valtora/Nojoin";

export const DEMO_RECORDING_OFF =
  "Recording is switched off in the demo. Install Nojoin to record your own meetings.";
