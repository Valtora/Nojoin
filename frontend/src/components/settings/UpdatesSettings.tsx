import { ButtonBase } from "@/components/ui/nativeBases";
import { useEffect, useId, useState, type ReactNode } from "react";
import { formatDistanceToNow } from "date-fns";
import { ChevronRight, ExternalLink, RefreshCw } from "lucide-react";

import MarkdownBubble from "@/components/MarkdownBubble";
import { getVersion } from "@/lib/api";
import { cn } from "@/lib/cn";
import { prepareReleaseNotes } from "@/lib/releaseNotes";
import { formatTimeZoneDate } from "@/lib/timezone";
import { useUserTimeZone } from "@/lib/useUserTimeZone";
import type { ReleaseInfo, UpdateStatus, VersionInfo } from "@/types";
import SettingsBlock from "./SettingsBlock";
import SettingsCallout from "./SettingsCallout";
import SettingsCard from "./SettingsCard";
import { useSettingsContext } from "./SettingsProvider";
import SettingsRow from "./SettingsRow";
import SettingsStatusBadge, {
  type SettingsStatusBadgeTone,
} from "./SettingsStatusBadge";
import { SETTINGS_BUTTON_SECONDARY } from "./settingsControls";
import { loadingButtonProps } from "@/components/ui/loadingButton";

const RELEASES_PAGE_URL = "https://github.com/Valtora/Nojoin/releases";
const DEPLOYMENT_GUIDE_URL =
  "https://github.com/Valtora/Nojoin/blob/main/docs/DEPLOYMENT.md";

const UPGRADE_COMMANDS = "docker compose pull\ndocker compose up -d";

const NOTES_CLASS =
  "prose prose-sm dark:prose-invert max-w-none focus:outline-none prose-headings:text-foreground prose-h3:text-sm prose-h3:font-semibold prose-h4:text-sm prose-p:text-contrast-muted prose-li:text-contrast-muted prose-li:my-1 prose-code:before:content-none prose-code:after:content-none";
const NOTES_LINK_CLASS =
  "text-action-text underline underline-offset-2 hover:text-action-text-hover";

const LINK_CLASS =
  "inline-flex items-center gap-1.5 rounded-sm text-sm font-medium text-action-text hover:text-action-text-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring";

function parseDate(value: string | null | undefined): Date | null {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function describePublished(value: string | null | undefined, timeZone: string): string {
  const date = parseDate(value);
  if (!date) {
    return "Publish date unavailable";
  }
  return `${formatTimeZoneDate(date, timeZone, "d MMM yyyy")}, ${formatDistanceToNow(date, { addSuffix: true })}`;
}

const STATUS: Record<
  UpdateStatus,
  { label: string; tone: SettingsStatusBadgeTone; copy: string }
> = {
  current: {
    label: "Up to date",
    tone: "success",
    copy: "This is the latest published release.",
  },
  "update-available": {
    label: "Update available",
    tone: "warning",
    copy: "A newer release has been published.",
  },
  ahead: {
    label: "Ahead of release",
    tone: "info",
    copy: "This build is newer than the latest published release.",
  },
  unknown: {
    label: "Status unavailable",
    tone: "neutral",
    copy: "The latest release could not be checked.",
  },
};

/**
 * A button that shows and hides its content. The content renders only while
 * open, so a long release history does not build an editor per release.
 */
function Disclosure({
  summary,
  defaultOpen = false,
  className,
  children,
}: {
  summary: ReactNode;
  defaultOpen?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const contentId = useId();

  return (
    <div className={className}>
      <ButtonBase
        type="button"
        aria-expanded={open}
        aria-controls={contentId}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 rounded-lg py-1 text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
      >
        <ChevronRight
          className={cn(
            "h-4 w-4 shrink-0 contrast-icon-muted transition-transform",
            open && "rotate-90",
          )}
          aria-hidden="true"
        />
        {summary}
      </ButtonBase>
      {open && (
        <div id={contentId} className="mt-3 pl-6">
          {children}
        </div>
      )}
    </div>
  );
}

function ReleaseNotes({ release }: { release: ReleaseInfo }) {
  if (!release.body?.trim()) {
    return (
      <p className="text-sm contrast-helper">
        No notes were published with this release.
      </p>
    );
  }

  const { images, notes } = prepareReleaseNotes(release.body);

  return (
    <div className="space-y-4">
      <MarkdownBubble
        content={notes}
        className={NOTES_CLASS}
        linkClassName={NOTES_LINK_CLASS}
        html={false}
      />
      {images && (
        <Disclosure
          summary={
            <span className="text-sm font-medium text-foreground">
              Container images
            </span>
          }
        >
          <MarkdownBubble
            content={images}
            className={NOTES_CLASS}
            linkClassName={NOTES_LINK_CLASS}
        html={false}
          />
        </Disclosure>
      )}
      <a
        href={release.html_url}
        target="_blank"
        rel="noopener noreferrer"
        className={LINK_CLASS}
      >
        <ExternalLink className="h-4 w-4" aria-hidden="true" />
        View {release.tag_name} on GitHub
      </a>
    </div>
  );
}

export default function UpdatesSettings() {
  const { isAdmin } = useSettingsContext();
  const timeZone = useUserTimeZone();
  const [versionInfo, setVersionInfo] = useState<VersionInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    } catch (fetchError: unknown) {
      console.error("Failed to fetch update metadata", fetchError);
      setError("Could not reach the release information.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void loadVersionInfo();
  }, []);

  const refreshButton = (
    <ButtonBase
      data-ui-fill="card"
      data-ui-hover="inset"
      data-ui-active="inset"
      data-ui-border="full"
      chrome="action"
      type="button"
      {...loadingButtonProps(refreshing, undefined, () => void loadVersionInfo(true))}
      className={SETTINGS_BUTTON_SECONDARY}
    >
      <RefreshCw
        className={cn("h-4 w-4", refreshing && "animate-spin")}
        aria-hidden="true"
      />
      {refreshing ? "Checking..." : "Check for updates"}
    </ButtonBase>
  );

  if (loading) {
    return (
      <SettingsCallout tone="neutral">
        <div className="flex items-center gap-3">
          <RefreshCw className="h-4 w-4 animate-spin" aria-hidden="true" />
          Loading release information...
        </div>
      </SettingsCallout>
    );
  }

  if (error || !versionInfo) {
    return (
      <SettingsCallout tone="error" title="Release information unavailable">
        <div className="space-y-3">
          <p>{error || "Could not reach the release information."} Check that this server can reach GitHub, then try again.</p>
          {refreshButton}
        </div>
      </SettingsCallout>
    );
  }

  const status = STATUS[versionInfo.update_status] ?? STATUS.unknown;
  const releases = versionInfo.releases ?? [];

  return (
    <>
      <SettingsCard
        id="updates-overview"
        title="Version"
        headerAside={refreshButton}
      >
        <SettingsRow
          label="Installed"
          description={status.copy}
          badge={
            <SettingsStatusBadge tone={status.tone}>
              {status.label}
            </SettingsStatusBadge>
          }
        >
          <p className="text-sm font-semibold tabular-nums text-foreground @min-[26rem]:text-right">
            {versionInfo.current_version}
          </p>
        </SettingsRow>

        <SettingsRow
          label="Latest release"
          description={
            versionInfo.latest_version
              ? `Published ${describePublished(versionInfo.latest_published_at, timeZone)}.`
              : "Not available."
          }
        >
          <p className="text-sm font-semibold tabular-nums text-foreground @min-[26rem]:text-right">
            {versionInfo.latest_version ?? "Unknown"}
          </p>
        </SettingsRow>

        {isAdmin && (
          <SettingsBlock
            id="updates-upgrade"
            label="How to upgrade"
            description="Pull the published images and recreate the stack. If you build from source, rebuild instead of pulling."
          >
            <div className="space-y-3">
              <pre className="settings-inset overflow-x-auto rounded-lg px-4 py-3 font-mono text-sm text-foreground">
                {UPGRADE_COMMANDS}
              </pre>
              <a
                href={DEPLOYMENT_GUIDE_URL}
                target="_blank"
                rel="noopener noreferrer"
                className={LINK_CLASS}
              >
                <ExternalLink className="h-4 w-4" aria-hidden="true" />
                Deployment guide
              </a>
            </div>
          </SettingsBlock>
        )}
      </SettingsCard>

      <SettingsCard
        id="updates-release-notes"
        title="Release notes"
        description="From GitHub Releases. The installed and latest releases are open."
        headerAside={
          <a
            href={RELEASES_PAGE_URL}
            target="_blank"
            rel="noopener noreferrer"
            className={SETTINGS_BUTTON_SECONDARY}
          >
            <ExternalLink className="h-4 w-4" aria-hidden="true" />
            All releases
          </a>
        }
      >
        {releases.length > 0 ? (
          releases.map((release) => {
            const isInstalled = release.version === versionInfo.current_version;
            const isLatest = release.version === versionInfo.latest_version;

            return (
              <SettingsBlock key={release.tag_name}>
                <Disclosure
                  defaultOpen={isInstalled || isLatest}
                  summary={
                    <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="text-sm font-semibold text-foreground">
                        {release.tag_name}
                      </span>
                      {isInstalled && (
                        <SettingsStatusBadge tone="success">
                          Installed
                        </SettingsStatusBadge>
                      )}
                      {isLatest && (
                        <SettingsStatusBadge tone="info">Latest</SettingsStatusBadge>
                      )}
                      <span className="text-xs contrast-helper">
                        {describePublished(release.published_at, timeZone)}
                      </span>
                    </span>
                  }
                >
                  <ReleaseNotes release={release} />
                </Disclosure>
              </SettingsBlock>
            );
          })
        ) : (
          <SettingsBlock>
            <p className="text-sm contrast-helper">
              No release history is available. This server may not be able to
              reach GitHub.
            </p>
          </SettingsBlock>
        )}
      </SettingsCard>
    </>
  );
}
