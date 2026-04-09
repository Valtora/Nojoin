import { useEffect, useState, type ReactNode } from "react";
import { format, formatDistanceToNow } from "date-fns";
import {
  ArrowUpCircle,
  Download,
  ExternalLink,
  GitBranch,
  RefreshCw,
} from "lucide-react";

import { getVersion } from "@/lib/api";
import { fuzzyMatch } from "@/lib/searchUtils";
import { ReleaseAsset, ReleaseInfo, VersionInfo } from "@/types";

const RELEASES_PAGE_URL = "https://github.com/Valtora/Nojoin/releases";
const DEPLOYMENT_GUIDE_URL =
  "https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md";

interface UpdatesSettingsProps {
  searchQuery?: string;
}

function getInstallerAsset(release: ReleaseInfo | null): ReleaseAsset | null {
  if (!release) {
    return null;
  }

  return (
    release.assets.find((asset) => {
      const assetName = asset.name.toLowerCase();
      return assetName.endsWith(".exe") && !assetName.includes("portable");
    }) || null
  );
}

function formatPublishedAt(value: string | null | undefined): string {
  if (!value) {
    return "Unknown publish date";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Unknown publish date";
  }

  return `${format(date, "dd MMM yyyy")}`;
}

function formatPublishedRelative(value: string | null | undefined): string {
  if (!value) {
    return "Publish date unavailable";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Publish date unavailable";
  }

  return formatDistanceToNow(date, { addSuffix: true });
}

function renderInlineMarkdown(text: string, keyPrefix: string) {
  const parts: Array<string | ReactNode> = [];
  const linkPattern = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null = null;
  let index = 0;

  while ((match = linkPattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }

    parts.push(
      <a
        key={`${keyPrefix}-link-${index}`}
        href={match[2]}
        target="_blank"
        rel="noopener noreferrer"
        className="text-orange-600 underline decoration-orange-300 underline-offset-2 hover:text-orange-700 dark:text-orange-400 dark:decoration-orange-700 dark:hover:text-orange-300"
      >
        {match[1]}
      </a>,
    );

    lastIndex = match.index + match[0].length;
    index += 1;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts;
}

function ReleaseNotes({ body, releaseVersion }: { body: string | null; releaseVersion: string }) {
  if (!body?.trim()) {
    return (
      <p className="text-sm text-gray-500 dark:text-gray-400">
        No release notes were published for this release.
      </p>
    );
  }

  const elements: ReactNode[] = [];
  const lines = body.replace(/\r/g, "").split("\n");
  const paragraphBuffer: string[] = [];
  const listBuffer: string[] = [];
  let elementIndex = 0;

  const flushParagraph = () => {
    if (!paragraphBuffer.length) {
      return;
    }

    const text = paragraphBuffer.join(" ");
    elements.push(
      <p
        key={`${releaseVersion}-paragraph-${elementIndex}`}
        className="text-sm leading-6 text-gray-700 dark:text-gray-300"
      >
        {renderInlineMarkdown(text, `${releaseVersion}-paragraph-${elementIndex}`)}
      </p>,
    );
    paragraphBuffer.length = 0;
    elementIndex += 1;
  };

  const flushList = () => {
    if (!listBuffer.length) {
      return;
    }

    elements.push(
      <ul
        key={`${releaseVersion}-list-${elementIndex}`}
        className="space-y-2 pl-5 text-sm text-gray-700 list-disc dark:text-gray-300"
      >
        {listBuffer.map((item, itemIndex) => (
          <li key={`${releaseVersion}-list-${elementIndex}-${itemIndex}`}>
            {renderInlineMarkdown(item, `${releaseVersion}-item-${elementIndex}-${itemIndex}`)}
          </li>
        ))}
      </ul>,
    );
    listBuffer.length = 0;
    elementIndex += 1;
  };

  lines.forEach((line) => {
    const trimmed = line.trim();

    if (!trimmed) {
      flushParagraph();
      flushList();
      return;
    }

    if (trimmed.startsWith("### ") || trimmed.startsWith("## ") || trimmed.startsWith("# ")) {
      flushParagraph();
      flushList();

      const title = trimmed.replace(/^#+\s*/, "");
      elements.push(
        <h5
          key={`${releaseVersion}-heading-${elementIndex}`}
          className="text-sm font-semibold uppercase tracking-wide text-gray-900 dark:text-white"
        >
          {title}
        </h5>,
      );
      elementIndex += 1;
      return;
    }

    if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      flushParagraph();
      listBuffer.push(trimmed.slice(2).trim());
      return;
    }

    flushList();
    paragraphBuffer.push(trimmed);
  });

  flushParagraph();
  flushList();

  return <div className="space-y-3">{elements}</div>;
}

function getStatusCopy(versionInfo: VersionInfo): string {
  switch (versionInfo.update_status) {
    case "update-available":
      return "A newer stable release is available.";
    case "ahead":
      return "This instance is ahead of the latest published stable release.";
    case "current":
      return "This instance is on the latest published stable release.";
    default:
      return "Release metadata could not be fully resolved.";
  }
}

export default function UpdatesSettings({
  searchQuery = "",
}: UpdatesSettingsProps) {
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const showUpdates = fuzzyMatch(searchQuery, [
    "update",
    "updates",
    "release",
    "releases",
    "version",
    "changelog",
    "release notes",
    "installer",
    "download",
    "github",
  ]);

  const loadVersionInfo = async (isRefresh = false) => {
    if (isRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      const data = await getVersion({ refresh: isRefresh });
      setVersionInfo(data);
      setError(null);
    } catch (fetchError) {
      console.error("Failed to fetch update metadata", fetchError);
      setError("Could not load update metadata.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void loadVersionInfo();
  }, []);

  const latestRelease = versionInfo?.releases?.[0] || null;
  const latestInstaller = getInstallerAsset(latestRelease);

  if (!showUpdates && searchQuery) {
    return <div className="text-gray-500">No matching settings found.</div>;
  }

  if (loading) {
    return (
      <div className="flex items-center gap-3 text-gray-500 dark:text-gray-400">
        <RefreshCw className="h-4 w-4 animate-spin" />
        Loading release information...
      </div>
    );
  }

  if (error || !versionInfo) {
    return (
      <div className="space-y-4">
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-300">
          {error || "Could not load update metadata."}
        </div>
        <button
          onClick={() => void loadVersionInfo(true)}
          className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
        >
          <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.85fr)]">
        <div className="flex flex-col gap-4 rounded-2xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800">
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div>
              <h3 className="flex items-center gap-2 text-lg font-semibold text-gray-900 dark:text-white">
                <ArrowUpCircle className="h-5 w-5 text-orange-500" />
                Updates & Releases
              </h3>
              <p className="mt-1 max-w-2xl text-sm text-gray-500 dark:text-gray-400">
                Track the installed version, the latest stable release, and the published release notes.
              </p>
            </div>
            <button
              onClick={() => void loadVersionInfo(true)}
              className="inline-flex items-center gap-2 self-start rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
            >
              <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-900/50">
              <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Installed Version
              </p>
              <p className="mt-2 text-2xl font-semibold text-gray-900 dark:text-white">
                {versionInfo.current_version}
              </p>
              <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                {getStatusCopy(versionInfo)}
              </p>
            </div>

            <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-900/50">
              <p className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Latest Stable Release
              </p>
              <p className="mt-2 text-2xl font-semibold text-gray-900 dark:text-white">
                {versionInfo.latest_version || "Unavailable"}
              </p>
              <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                {formatPublishedRelative(versionInfo.latest_published_at)}
              </p>
            </div>
          </div>

          <div className="flex flex-wrap gap-3">
            <a
              href={versionInfo.release_url || RELEASES_PAGE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-lg bg-orange-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-orange-700"
            >
              <ExternalLink className="h-4 w-4" />
              View Latest Release
            </a>
            <a
              href={RELEASES_PAGE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
            >
              <GitBranch className="h-4 w-4" />
              Browse All Releases
            </a>
            {(latestInstaller || versionInfo.companion_download_url) && (
              <a
                href={
                  latestInstaller?.browser_download_url ||
                  versionInfo.companion_download_url ||
                  RELEASES_PAGE_URL
                }
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
              >
                <Download className="h-4 w-4" />
                Download Companion
              </a>
            )}
          </div>
        </div>

        <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800">
          <h4 className="text-base font-semibold text-gray-900 dark:text-white">
            Server Update Guidance
          </h4>
          <p className="mt-3 text-sm leading-6 text-gray-600 dark:text-gray-300">
            Most hosted installations update by pulling the published container images and restarting the stack. If you build from source instead, rebuild locally rather than pulling from GHCR.
          </p>
          <div className="mt-4 rounded-xl bg-gray-950 p-4 text-sm text-gray-100 dark:bg-gray-950">
            <code>docker compose pull && docker compose up -d</code>
          </div>
          <a
            href={DEPLOYMENT_GUIDE_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-4 inline-flex items-center gap-2 text-sm font-medium text-orange-600 hover:text-orange-700 dark:text-orange-400 dark:hover:text-orange-300"
          >
            <ExternalLink className="h-4 w-4" />
            Read the deployment guide
          </a>
        </div>
      </div>

      <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h4 className="text-base font-semibold text-gray-900 dark:text-white">
                Latest Release Snapshot
              </h4>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                The latest stable release metadata is sourced from GitHub Releases.
              </p>
            </div>
            <span className="inline-flex min-w-24 items-center justify-center rounded-full bg-orange-100 px-3 py-1 text-center text-xs font-semibold leading-tight text-orange-700 dark:bg-orange-900/30 dark:text-orange-300">
              {versionInfo.update_status === "update-available"
                ? "Update available"
                : versionInfo.update_status === "ahead"
                  ? "Ahead of stable"
                  : versionInfo.update_status === "current"
                    ? "Up to date"
                    : "Status unavailable"}
            </span>
          </div>

          {latestRelease ? (
            <div className="mt-6 space-y-5">
              <div className="flex flex-col gap-3 rounded-xl border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-900/50">
                <div className="flex flex-wrap items-center gap-3">
                  <h5 className="text-xl font-semibold text-gray-900 dark:text-white">
                    {latestRelease.tag_name}
                  </h5>
                  <span className="rounded-full bg-gray-200 px-2.5 py-1 text-xs font-medium text-gray-700 dark:bg-gray-700 dark:text-gray-200">
                    Published {formatPublishedAt(latestRelease.published_at)}
                  </span>
                  {versionInfo.current_version === latestRelease.version && (
                    <span className="rounded-full bg-green-100 px-2.5 py-1 text-xs font-medium text-green-700 dark:bg-green-900/30 dark:text-green-300">
                      Installed
                    </span>
                  )}
                </div>

                <div className="flex flex-wrap gap-3">
                  <a
                    href={latestRelease.html_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
                  >
                    <ExternalLink className="h-4 w-4" />
                    Open on GitHub
                  </a>
                  {latestInstaller && (
                    <a
                      href={latestInstaller.browser_download_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
                    >
                      <Download className="h-4 w-4" />
                      Windows Installer
                    </a>
                  )}
                </div>
              </div>

              <ReleaseNotes
                body={latestRelease.body}
                releaseVersion={latestRelease.version}
              />
            </div>
          ) : (
            <p className="mt-6 text-sm text-gray-500 dark:text-gray-400">
              Release history is not available right now. Version checks are using the fallback metadata source.
            </p>
          )}
      </div>

      <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm dark:border-gray-700 dark:bg-gray-800">
        <h4 className="text-base font-semibold text-gray-900 dark:text-white">
          Release History
        </h4>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          Recent Nojoin releases published on GitHub.
        </p>

        <div className="mt-6 space-y-4">
          {versionInfo.releases.length > 0 ? (
            versionInfo.releases.map((release) => {
              const installerAsset = getInstallerAsset(release);
              const isInstalled = release.version === versionInfo.current_version;
              const isLatest = release.version === versionInfo.latest_version;

              return (
                <div
                  key={release.tag_name}
                  className="rounded-xl border border-gray-200 bg-gray-50 p-5 dark:border-gray-700 dark:bg-gray-900/40"
                >
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h5 className="text-lg font-semibold text-gray-900 dark:text-white">
                          {release.tag_name}
                        </h5>
                        {isInstalled && (
                          <span className="rounded-full bg-green-100 px-2.5 py-1 text-xs font-medium text-green-700 dark:bg-green-900/30 dark:text-green-300">
                            Installed
                          </span>
                        )}
                        {isLatest && (
                          <span className="rounded-full bg-orange-100 px-2.5 py-1 text-xs font-medium text-orange-700 dark:bg-orange-900/30 dark:text-orange-300">
                            Latest
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                        Published {formatPublishedAt(release.published_at)} • {formatPublishedRelative(release.published_at)}
                      </p>
                    </div>

                    <div className="flex flex-wrap gap-3">
                      <a
                        href={release.html_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
                      >
                        <ExternalLink className="h-4 w-4" />
                        View Release
                      </a>
                      {installerAsset && (
                        <a
                          href={installerAsset.browser_download_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
                        >
                          <Download className="h-4 w-4" />
                          Installer
                        </a>
                      )}
                    </div>
                  </div>

                  <div className="mt-4 max-h-72 overflow-y-auto rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800/70">
                    <ReleaseNotes
                      body={release.body}
                      releaseVersion={release.version}
                    />
                  </div>
                </div>
              );
            })
          ) : (
            <p className="text-sm text-gray-500 dark:text-gray-400">
              Release history is unavailable right now.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}