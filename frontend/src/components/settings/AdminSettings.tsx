import { fuzzyMatch } from "@/lib/searchUtils";
import UsersTab from "./UsersTab";
import CliUsageTab from "./CliUsageTab";
import InvitesTab from "./InvitesTab";
import BackupRestore from "./BackupRestore";
import SystemTab from "./SystemTab";
import TelemetrySection from "./TelemetrySection";
import CalendarProviderSettings from "./CalendarProviderSettings";
import SettingsCallout from "./SettingsCallout";
import SettingsSection from "./SettingsSection";

interface AdminSettingsProps {
  isAdmin: boolean;
  searchQuery?: string;
}

export default function AdminSettings({
  isAdmin,
  searchQuery = "",
}: AdminSettingsProps) {
  if (!isAdmin) {
    return null;
  }

  const showUsers = !searchQuery || fuzzyMatch(searchQuery, [
    "users",
    "roles",
    "permissions",
    "access",
    "superuser",
  ]);
  const showUsage = !searchQuery || fuzzyMatch(searchQuery, [
    "cli usage",
    "usage",
    "tokens",
    "quota",
    "subscription",
    "claude subscription",
    "consumption",
    "rate limit",
  ]);
  const showInvites = !searchQuery || fuzzyMatch(searchQuery, [
    "invite",
    "token",
    "link",
    "join",
    "registration",
  ]);
  const showCalendar = !searchQuery || fuzzyMatch(searchQuery, [
    "calendar",
    "gmail",
    "google",
    "outlook",
    "microsoft",
    "oauth",
    "provider",
  ]);
  const showSystem = !searchQuery || fuzzyMatch(searchQuery, [
    "system",
    "logs",
    "infrastructure",
    "docker",
    "port",
    "redis",
    "worker",
  ]);
  const showTelemetry = !searchQuery || fuzzyMatch(searchQuery, [
    "telemetry",
    "usage data",
    "anonymous",
    "analytics",
    "privacy",
    "opt out",
    "phone home",
    "tracking",
    "statistics",
  ]);
  const showBackup = !searchQuery || fuzzyMatch(searchQuery, [
    "backup",
    "restore",
    "export",
    "import",
    "archive",
    "recovery",
    "data",
  ]);

  if (
    !showUsers &&
    !showUsage &&
    !showInvites &&
    !showCalendar &&
    !showSystem &&
    !showTelemetry &&
    !showBackup
  ) {
    return (
      <SettingsCallout
        tone="neutral"
        title="No matching settings"
        message="Try a broader search term for users, invitations, calendar providers, logs, backups, or restore operations."
      />
    );
  }

  return (
    <div className="space-y-8">
      {showUsers && (
        <SettingsSection
          eyebrow="Administration"
          title="Users"
          description="Create, edit, and review account access across the installation."
          width="full"
        >
          <UsersTab />
        </SettingsSection>
      )}

      {showUsage && (
        <SettingsSection
          eyebrow="Administration"
          title="CLI usage & quota"
          description="Per-user subscription (CLI OAuth) token usage and rate-limit status, across Claude and ChatGPT."
          width="full"
        >
          <CliUsageTab />
        </SettingsSection>
      )}

      {showInvites && (
        <SettingsSection
          eyebrow="Administration"
          title="Invitations"
          description="Issue and revoke invitation links for new user sign-ups."
          width="wide"
        >
          <InvitesTab />
        </SettingsSection>
      )}

      {showCalendar && (
        <SettingsSection
          eyebrow="Administration"
          title="Calendar providers"
          description="Configure installation-wide OAuth credentials for Google and Microsoft calendar connections."
          width="wide"
        >
          <CalendarProviderSettings />
        </SettingsSection>
      )}

      {showSystem && (
        <SettingsSection
          eyebrow="Administration"
          title="System operations"
          description="Inspect live logs and operational output from the Nojoin services."
          width="full"
        >
          <SystemTab />
        </SettingsSection>
      )}

      {showTelemetry && <TelemetrySection />}

      {showBackup && (
        <SettingsSection
          eyebrow="Administration"
          title="Backup and restore"
          description="Export or recover application data with explicit, transactional actions."
          width="wide"
        >
          <BackupRestore />
        </SettingsSection>
      )}
    </div>
  );
}
