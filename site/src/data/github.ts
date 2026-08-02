// Build-time GitHub data. Both values have fallbacks because a scheduled
// rebuild must never fail on a rate-limited or unreachable API: the release
// falls back to docs/VERSION from the checkout, and a missing star count
// renders the button without a number, which is a legitimate state rather
// than a placeholder.
import versionRaw from "../../../docs/VERSION?raw";

const API = "https://api.github.com/repos/Valtora/Nojoin";

async function getJson(path: string): Promise<Record<string, unknown> | null> {
  const token = process.env.GITHUB_TOKEN;
  try {
    const response = await fetch(`${API}${path}`, {
      headers: {
        Accept: "application/vnd.github+json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) return null;
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export async function getStarCount(): Promise<number | null> {
  const repo = await getJson("");
  const stars = repo?.stargazers_count;
  return typeof stars === "number" ? stars : null;
}

export function formatStarCount(stars: number): string {
  if (stars < 1000) return String(stars);
  return `${(stars / 1000).toFixed(stars < 10000 ? 1 : 0)}k`;
}

export async function getLatestVersion(): Promise<string> {
  const release = await getJson("/releases/latest");
  const tag = release?.tag_name;
  if (typeof tag === "string" && tag.length > 0) return tag.replace(/^v/, "");
  return versionRaw.trim();
}
