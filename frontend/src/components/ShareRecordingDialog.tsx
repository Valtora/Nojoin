"use client";

import { Trash2, UserPlus, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import Select from "@/components/ui/Select";
import {
  deleteShare,
  getGroupDirectory,
  getUserDirectory,
  listShares,
  upsertShare,
  type DirectoryGroup,
  type RecordingShare,
  type ShareLevel,
  type ShareUser,
} from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import type { RecordingId } from "@/types";

interface ShareRecordingDialogProps {
  recordingId: RecordingId;
  recordingName: string;
  open: boolean;
  onClose: () => void;
}

const LEVEL_LABELS: Record<ShareLevel, string> = {
  view: "Can view",
  edit: "Can edit",
};

const userLabel = (user: ShareUser) =>
  user.display_name ? `${user.display_name} (${user.username})` : user.username;

/**
 * Who else can open a meeting. Only the owner sees this dialog. People who
 * can edit fix the transcript, speakers and notes; processing, AI generation,
 * deleting and sharing stay with the owner.
 */
export default function ShareRecordingDialog({
  recordingId,
  recordingName,
  open,
  onClose,
}: ShareRecordingDialogProps) {
  const [shares, setShares] = useState<RecordingShare[]>([]);
  const [people, setPeople] = useState<ShareUser[]>([]);
  const [groups, setGroups] = useState<DirectoryGroup[]>([]);
  const [grantee, setGrantee] = useState("");
  const [level, setLevel] = useState<ShareLevel>("edit");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) {
      return;
    }
    let cancelled = false;
    Promise.all([listShares(recordingId), getUserDirectory(), getGroupDirectory()])
      .then(([current, directory, groupDirectory]) => {
        if (cancelled) return;
        setShares(current);
        setPeople(directory);
        setGroups(groupDirectory);
        setError(null);
      })
      .catch((loadError: unknown) => {
        if (!cancelled) setError(getErrorMessage(loadError, "Could not load who has access."));
      });
    return () => {
      cancelled = true;
    };
  }, [open, recordingId]);

  const sharedUserIds = useMemo(
    () => new Set(shares.flatMap((share) => (share.user ? [share.user.id] : []))),
    [shares],
  );
  const sharedGroups = useMemo(
    () => new Set(shares.flatMap((share) => (share.group ? [share.group] : []))),
    [shares],
  );
  const availablePeople = people.filter((person) => !sharedUserIds.has(person.id));
  const availableGroups = groups.filter((group) => !sharedGroups.has(group.name));

  const run = async (action: () => Promise<RecordingShare[]>) => {
    setBusy(true);
    setError(null);
    try {
      setShares(await action());
      return true;
    } catch (actionError: unknown) {
      setError(getErrorMessage(actionError, "That did not work. Please try again."));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const handleAdd = async () => {
    if (!grantee) return;
    const [kind, value] = [grantee.slice(0, grantee.indexOf(":")), grantee.slice(grantee.indexOf(":") + 1)];
    const target = kind === "user" ? { user_id: Number(value) } : { group: value };
    if (await run(() => upsertShare(recordingId, target, level))) {
      setGrantee("");
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Share meeting"
      description={recordingName}
      footer={
        <Button variant="secondary" onClick={onClose}>
          Done
        </Button>
      }
    >
      <div className="space-y-5">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1">
            <Select
              label="Add people or a group"
              value={grantee}
              onChange={(event) => setGrantee(event.target.value)}
              disabled={busy}
            >
              <option value="">Choose…</option>
              {availablePeople.length > 0 ? (
                <optgroup label="People">
                  {availablePeople.map((person) => (
                    <option key={person.id} value={`user:${person.id}`}>
                      {userLabel(person)}
                    </option>
                  ))}
                </optgroup>
              ) : null}
              {availableGroups.length > 0 ? (
                <optgroup label="Your groups">
                  {availableGroups.map((group) => (
                    <option key={group.name} value={`group:${group.name}`}>
                      {group.name} ({group.member_count}{" "}
                      {group.member_count === 1 ? "member" : "members"})
                    </option>
                  ))}
                </optgroup>
              ) : null}
            </Select>
          </div>
          <Select
            label="Access"
            value={level}
            onChange={(event) => setLevel(event.target.value as ShareLevel)}
            disabled={busy}
          >
            <option value="edit">{LEVEL_LABELS.edit}</option>
            <option value="view">{LEVEL_LABELS.view}</option>
          </Select>
          <Button
            variant="primary"
            onClick={() => void handleAdd()}
            disabled={!grantee || busy}
            iconLeft={<UserPlus aria-hidden="true" className="h-4 w-4" />}
          >
            Share
          </Button>
        </div>

        {error ? (
          <p role="alert" className="text-sm text-status-danger-fg">
            {error}
          </p>
        ) : null}

        <div>
          <h3 className="mb-2 text-sm font-semibold text-foreground">Who has access</h3>
          {shares.length === 0 ? (
            <p className="text-sm text-contrast-helper">Only you.</p>
          ) : (
            <ul className="divide-y divide-surface-border rounded-lg border border-surface-border">
              {shares.map((share) => (
                <li key={share.id} className="flex items-center gap-3 px-3 py-2">
                  {share.group ? (
                    <Users aria-hidden="true" className="h-4 w-4 shrink-0 text-contrast-icon-muted" />
                  ) : null}
                  <span className="min-w-0 flex-1 truncate text-sm text-foreground">
                    {share.user ? userLabel(share.user) : `Everyone in ${share.group}`}
                  </span>
                  <label className="sr-only" htmlFor={`share-level-${share.id}`}>
                    Access for {share.user ? share.user.username : share.group}
                  </label>
                  <select
                    id={`share-level-${share.id}`}
                    className="rounded-md border border-control-border bg-control-bg px-2 py-1 text-sm text-foreground"
                    value={share.level}
                    disabled={busy}
                    onChange={(event) =>
                      void run(() =>
                        upsertShare(
                          recordingId,
                          share.user ? { user_id: share.user.id } : { group: share.group as string },
                          event.target.value as ShareLevel,
                        ),
                      )
                    }
                  >
                    <option value="edit">{LEVEL_LABELS.edit}</option>
                    <option value="view">{LEVEL_LABELS.view}</option>
                  </select>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`Remove ${share.user ? share.user.username : share.group}`}
                    disabled={busy}
                    onClick={() => void run(() => deleteShare(recordingId, share.id))}
                    iconLeft={<Trash2 aria-hidden="true" className="h-4 w-4" />}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>

        <p className="text-xs leading-5 text-contrast-helper">
          People who can edit can fix the transcript, rename speakers and edit the notes.
          Reprocessing, generating notes with AI, deleting and sharing stay with you.
          Chat and tags stay personal to each person.
        </p>
      </div>
    </Modal>
  );
}
