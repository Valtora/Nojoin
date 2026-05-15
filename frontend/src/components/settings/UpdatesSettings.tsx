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
import { ReleaseAsset, ReleaseInfo, UpdateStatus, VersionInfo } from "@/types";
import SettingsCallout from "./SettingsCallout";
import SettingsPanel from "./SettingsPanel";
import SettingsSection from "./SettingsSection";
import SettingsStatusBadge, {
  type SettingsStatusBadgeTone,
} from "./SettingsStatusBadge";

const RELEASES_PAGE_URL = "https://github.com/Valtora/Nojoin/releases";
const DEPLOYMENT_GUIDE_URL =
  "https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md";

const PRIMARY_ACTION_STYLES =
  "inline-flex items-center gap-2 rounded-xl bg-orange-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-orange-700";

const SECONDARY_ACTION_STYLES =
  "inline-flex items-center gap-2 rounded-xl border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800";

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
      <p className="text-sm contrast-helper">
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

function getUpdateStatusTone(
  status: UpdateStatus,
): SettingsStatusBadgeTone {
  switch (status) {
    case "update-available":
      return "warning";
    case "ahead":
      return "info";
    case "current":
      return "success";
    default:
      return "neutral";
  }
}

function getUpdateStatusLabel(status: UpdateStatus): string {
  switch (status) {
    case "update-available":
      return "Update available";
    case "ahead":
      return "Ahead of stable";
    case "current":
      return "Up to date";
    default:
      return "Status unavailable";
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
    return (
      <SettingsCallout
        tone="neutral"
        title="No matching settings"
        message="Try a broader search term for releases, versions, downloads, or installers."
      />
    );
  }

  if (loading) {
    return (
      <SettingsCallout tone="neutral">
        <div className="flex items-center gap-3">
          <RefreshCw className="h-4 w-4 animate-spin" />
          Loading release information...
        </div>
      </SettingsCallout>
    );
  }

  if (error || !versionInfo) {
    return (
      <SettingsCallout tone="error" title="Update metadata unavailable">
        <div className="space-y-3">
          <p>{error || "Could not load update metadata."}</p>
          <button
            onClick={() => void loadVersionInfo(true)}
            className={SECONDARY_ACTION_STYLES}
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
            Retry
          </button>
        </div>
      </SettingsCallout>
    );
  }

  return (
    <div className="space-y-8">
      <SettingsSection
        eyebrow="Updates"
        title="Release overview"
        description="Track the installed version, the latest stable release, and the published download links."
        width="full"
        headerAside={
          <button
            onClick={() => void loadVersionInfo(true)}
            className={SECONDARY_ACTION_STYLES}
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
            Refresh
          </button>
        }
      >
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.85fr)]">
          <div className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <SettingsPanel variant="subtle">
                <p className="text-xs font-semibold uppercase tracking-wide contrast-helper">
                  Installed version
                </p>
                <p className="mt-2 text-2xl font-semibold text-gray-900 dark:text-white">
                  {versionInfo.current_version}
                </p>
                <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                  {getStatusCopy(versionInfo)}
                </p>
              </SettingsPanel>

              <SettingsPanel variant="subtle">
                <p className="text-xs font-semibold uppercase tracking-wide contrast-helper">
                  Latest stable release
                </p>
                <p className="mt-2 text-2xl font-semibold text-gray-900 dark:text-white">
                  {versionInfo.latest_version || "Unavailable"}
                </p>
                <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                  {formatPublishedRelative(versionInfo.latest_published_at)}
                </p>
              </SettingsPanel>
            </div>

            <SettingsPanel className="space-y-4">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                  Release links
                </div>
                <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
                  Open the published release pages, compare versions, or download the latest Companion installer directly.
                </p>
              </div>

              <div className="flex flex-wrap gap-3">
                <a
                  href={versionInfo.release_url || RELEASES_PAGE_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={PRIMARY_ACTION_STYLES}
                >
                  <ExternalLink className="h-4 w-4" />
                  View latest release
                </a>
                <a
                  href={RELEASES_PAGE_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={SECONDARY_ACTION_STYLES}
                >
                  <GitBranch className="h-4 w-4" />
                  Browse all releases
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
                    className={SECONDARY_ACTION_STYLES}
                  >
                    <Download className="h-4 w-4" />
                    Download Companion
                  </a>
                )}
              </div>
            </SettingsPanel>
          </div>

          <SettingsPanel variant="subtle" className="space-y-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-900 dark:text-white">
              <ArrowUpCircle className="h-4 w-4 text-orange-500" />
              Server update guidance
            </div>
            <p className="text-sm leading-6 text-gray-600 dark:text-gray-300">
              Most hosted installations update by pulling the published container images and restarting the stack. If you build from source instead, rebuild locally rather than pulling from GHCR.
            </p>
            <div className="rounded-2xl bg-gray-950 p-4 text-sm text-gray-100 dark:bg-black">
              <code>docker compose pull && docker compose up -d</code>
            </div>
            <a
              href={DEPLOYMENT_GUIDE_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 text-sm font-medium text-orange-600 hover:text-orange-700 dark:text-orange-400 dark:hover:text-orange-300"
            >
              <ExternalLink className="h-4 w-4" />
              Read the deployment guide
            </a>
          </SettingsPanel>
        </div>
      </SettingsSection>

      <SettingsSection
        eyebrow="Updates"
        title="Latest release snapshot"
        description="The latest stable release metadata is sourced from GitHub Releases."
        width="full"
        badge={
          <SettingsStatusBadge tone={getUpdateStatusTone(versionInfo.update_status)}>
            {getUpdateStatusLabel(versionInfo.update_status)}
          </SettingsStatusBadge>
        }
      >
        {latestRelease ? (
          <div className="space-y-5">
            <SettingsPanel variant="subtle" className="space-y-4">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <div className="flex flex-wrap items-center gap-3">
                    <h5 className="text-xl font-semibold text-gray-900 dark:text-white">
                      {latestRelease.tag_name}
                    </h5>
                    <SettingsStatusBadge tone="neutral">
                      Published {formatPublishedAt(latestRelease.published_at)}
                    </SettingsStatusBadge>
                    {versionInfo.current_version === latestRelease.version && (
                      <SettingsStatusBadge tone="success">
                        Installed
                      </SettingsStatusBadge>
                    )}
                  </div>
                  <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                    Published {formatPublishedRelative(latestRelease.published_at)}.
                  </p>
                </div>

                <div className="flex flex-wrap gap-3">
                  <a
                    href={latestRelease.html_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={SECONDARY_ACTION_STYLES}
                  >
                    <ExternalLink className="h-4 w-4" />
                    Open on GitHub
                  </a>
                  {latestInstaller && (
                    <a
                      href={latestInstaller.browser_download_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={SECONDARY_ACTION_STYLES}
                    >
                      <Download className="h-4 w-4" />
                      Windows installer
                    </a>
                  )}
                </div>
              </div>
            </SettingsPanel>

            <SettingsPanel>
              <ReleaseNotes
                body={latestRelease.body}
                releaseVersion={latestRelease.version}
              />
            </SettingsPanel>
          </div>
        ) : (
          <SettingsCallout
            tone="neutral"
            message="Release history is not available right now. Version checks are using the fallback metadata source."
          />
        )}
      </SettingsSection>

      <SettingsSection
        eyebrow="Updates"
        title="Release history"
        description="Recent Nojoin releases published on GitHub."
        width="full"
      >
        {versionInfo.releases.length > 0 ? (
          <div className="space-y-4">
            {versionInfo.releases.map((release) => {
              const installerAsset = getInstallerAsset(release);
              const isInstalled = release.version === versionInfo.current_version;
              const isLatest = release.version === versionInfo.latest_version;

              return (
                <SettingsPanel key={release.tag_name} className="space-y-4">
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h5 className="text-lg font-semibold text-gray-900 dark:text-white">
                          {release.tag_name}
                        </h5>
                        {isInstalled && (
                          <SettingsStatusBadge tone="success">
                            Installed
                          </SettingsStatusBadge>
                        )}
                        {isLatest && (
                          <SettingsStatusBadge tone="warning">
                            Latest
                          </SettingsStatusBadge>
                        )}
                      </div>
                      <p className="mt-1 text-sm contrast-helper">
                        Published {formatPublishedAt(release.published_at)} • {formatPublishedRelative(release.published_at)}
                      </p>
                    </div>

                    <div className="flex flex-wrap gap-3">
                      <a
                        href={release.html_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className={SECONDARY_ACTION_STYLES}
                      >
                        <ExternalLink className="h-4 w-4" />
                        View release
                      </a>
                      {installerAsset && (
                        <a
                          href={installerAsset.browser_download_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className={SECONDARY_ACTION_STYLES}
                        >
                          <Download className="h-4 w-4" />
                          Installer
                        </a>
                      )}
                    </div>
                  </div>

                  <SettingsPanel
                    variant="subtle"
                    className="max-h-72 overflow-y-auto"
                  >
                    <ReleaseNotes
                      body={release.body}
                      releaseVersion={release.version}
                    />
                  </SettingsPanel>
                </SettingsPanel>
              );
            })}
          </div>
        ) : (
          <SettingsCallout
            tone="neutral"
            message="Release history is unavailable right now."
          />
        )}
      </SettingsSection>
    </div>
  );
}