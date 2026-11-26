"use client";

import { Plus, Search, Trash2, User, Users } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import Select from "@/components/ui/Select";
import { ButtonBase } from "@/components/ui/nativeBases";
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
  const [query, setQuery] = useState("");
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
  const needle = query.trim().toLowerCase();
  const matchingPeople = availablePeople.filter(
    (person) =>
      !needle ||
      person.username.toLowerCase().includes(needle) ||
      (person.display_name ?? "").toLowerCase().includes(needle),
  );
  const matchingGroups = availableGroups.filter(
    (group) => !needle || group.name.toLowerCase().includes(needle),
  );
  const nobodyElse = people.length === 0 && groups.length === 0;
  const everyoneAdded = !nobodyElse && availablePeople.length === 0 && availableGroups.length === 0;

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

  const add = async (target: { user_id: number } | { group: string }) => {
    if (await run(() => upsertShare(recordingId, target, level))) {
      setQuery("");
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
        {nobodyElse ? (
          <p className="text-sm text-contrast-helper">
            Nobody else has an account here yet. Once an administrator adds people, or
            they sign in with single sign-on, you can share with them and with your
            groups here.
          </p>
        ) : everyoneAdded ? (
          <p className="text-sm text-contrast-helper">
            Everyone you can share with already has access.
          </p>
        ) : (
          <div className="space-y-2">
            <div className="flex gap-2">
              <div className="min-w-0 flex-1">
                <Input
                  type="search"
                  aria-label="Search people and groups"
                  placeholder="Search people and groups"
                  iconLeft={<Search />}
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  disabled={busy}
                />
              </div>
              <div className="w-32 shrink-0">
                <Select
                  aria-label="Access for people you add"
                  value={level}
                  onChange={(event) => setLevel(event.target.value as ShareLevel)}
                  disabled={busy}
                >
                  <option value="edit">{LEVEL_LABELS.edit}</option>
                  <option value="view">{LEVEL_LABELS.view}</option>
                </Select>
              </div>
            </div>
            <div className="max-h-60 overflow-y-auto rounded-lg border border-surface-border">
              {matchingPeople.length === 0 && matchingGroups.length === 0 ? (
                <p className="px-3 py-3 text-sm text-contrast-helper">
                  No people or groups match &quot;{query.trim()}&quot;.
                </p>
              ) : (
                <ul aria-label="People and groups you can add" className="divide-y divide-surface-border">
                  {matchingPeople.map((person) => (
                    <CandidateRow
                      key={`user:${person.id}`}
                      icon={<User aria-hidden="true" className="h-4 w-4" />}
                      name={person.display_name || person.username}
                      detail={person.display_name ? person.username : undefined}
                      disabled={busy}
                      onAdd={() => void add({ user_id: person.id })}
                    />
                  ))}
                  {matchingGroups.map((group) => (
                    <CandidateRow
                      key={`group:${group.name}`}
                      icon={<Users aria-hidden="true" className="h-4 w-4" />}
                      name={group.name}
                      detail={`Group · ${group.member_count} ${
                        group.member_count === 1 ? "member" : "members"
                      }`}
                      disabled={busy}
                      onAdd={() => void add({ group: group.name })}
                    />
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}

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

function CandidateRow({
  icon,
  name,
  detail,
  disabled,
  onAdd,
}: {
  icon: ReactNode;
  name: string;
  detail?: string;
  disabled: boolean;
  onAdd: () => void;
}) {
  return (
    <li>
      <ButtonBase
        type="button"
        disabled={disabled}
        onClick={onAdd}
        aria-label={`Share with ${name}`}
        className="flex w-full items-center gap-3 px-3 py-2 text-left transition-colors hover:bg-surface-inset disabled:opacity-60"
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-inset text-contrast-icon-muted">
          {icon}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-foreground">{name}</span>
          {detail ? (
            <span className="block truncate text-xs text-contrast-helper">{detail}</span>
          ) : null}
        </span>
        <Plus aria-hidden="true" className="h-4 w-4 shrink-0 text-contrast-icon-muted" />
      </ButtonBase>
    </li>
  );
}
