"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { useState, useEffect, useCallback } from "react";
import { Invitation, UserRole } from "@/types";
import {
  getInvitations,
  createInvitation,
  revokeInvitation,
  deleteInvitation,
} from "@/lib/api";
import { sanitizeIntegerString } from "@/lib/validation";
import { Plus, Trash2, Copy, XCircle } from "lucide-react";
import ConfirmationModal from "../ConfirmationModal";
import Button from "../ui/Button";
import Input from "../ui/Input";
import Modal from "../ui/Modal";
import Select from "../ui/Select";
import { useNotificationStore } from "@/lib/notificationStore";
import { parseServerTimestamp } from "@/lib/serverTimestamp";
import { formatTimeZoneLocale } from "@/lib/timezone";
import { useUserTimeZone } from "@/lib/useUserTimeZone";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import SettingsStatusBadge from "./SettingsStatusBadge";
import {
  SETTINGS_BUTTON_DANGER,
  SETTINGS_BUTTON_PRIMARY,
  SETTINGS_BUTTON_SECONDARY,
} from "./settingsControls";

export default function InvitesTab() {
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const { addNotification } = useNotificationStore();
  const timeZone = useUserTimeZone();

  // Create Form State
  const [role, setRole] = useState<UserRole>(UserRole.USER);
  const [expiresIn, setExpiresIn] = useState(7);
  const [maxUses, setMaxUses] = useState(1);
  const [creating, setCreating] = useState(false);

  // Revoke State
  const [revokeModalOpen, setRevokeModalOpen] = useState(false);
  const [inviteToRevoke, setInviteToRevoke] = useState<number | null>(null);

  // Delete State
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [inviteToDelete, setInviteToDelete] = useState<number | null>(null);

  const fetchInvitations = useCallback(async () => {
    try {
      const data = await getInvitations();
      setInvitations(data);

        } catch (e: unknown) {
      console.error("Failed to fetch invitations", e);
      addNotification({
        type: "error",
        message: "Failed to load invitations",
      });
    } finally {
      setLoading(false);
    }
  }, [addNotification]);

  useEffect(() => {
    void fetchInvitations();
  }, [fetchInvitations]);

  const handleCreate = async () => {
    setCreating(true);
    try {
      await createInvitation(role, expiresIn, maxUses);
      await fetchInvitations();
      setShowCreateModal(false);
      addNotification({
        type: "success",
        message: "Invitation created successfully",
      });

        } catch (e: unknown) {
      console.error("Failed to create invitation", e);
      addNotification({
        type: "error",
        message: "Failed to create invitation",
      });
    } finally {
      setCreating(false);
    }
  };

  const handleRevokeClick = (id: number) => {
    setInviteToRevoke(id);
    setRevokeModalOpen(true);
  };

  const confirmRevoke = async () => {
    if (!inviteToRevoke) return;
    try {
      await revokeInvitation(inviteToRevoke);
      await fetchInvitations();
      addNotification({
        type: "success",
        message: "Invitation revoked",
      });

        } catch (e: unknown) {
      console.error("Failed to revoke invitation", e);
      addNotification({
        type: "error",
        message: "Failed to revoke invitation",
      });
    } finally {
      setRevokeModalOpen(false);
      setInviteToRevoke(null);
    }
  };

  const handleDeleteClick = (id: number) => {
    setInviteToDelete(id);
    setDeleteModalOpen(true);
  };

  const confirmDelete = async () => {
    if (!inviteToDelete) return;
    try {
      await deleteInvitation(inviteToDelete);
      await fetchInvitations();
      addNotification({
        type: "success",
        message: "Invitation deleted",
      });

        } catch (e: unknown) {
      console.error("Failed to delete invitation", e);
      addNotification({
        type: "error",
        message: "Failed to delete invitation",
      });
    } finally {
      setDeleteModalOpen(false);
      setInviteToDelete(null);
    }
  };

  const copyLink = (link: string) => {
    navigator.clipboard
      .writeText(link)
      .then(() => {
        addNotification({
          type: "success",
          message: "Invitation link copied",
        });
      })
      .catch(() => {
        addNotification({
          type: "error",
          message: "Failed to copy invitation link",
        });
      });
  };

  return (
    <SettingsCard
      id="users-invitations"
      title="Invitations"
      description="Sign-up links for new people. Revoke one to stop it working."
      headerAside={
        <ButtonBase
          data-ui-fill="action"
          data-ui-hover="action-hover"
          data-ui-active="action-active"
          data-ui-border="full"
          chrome="action"
          type="button"
          onClick={() => setShowCreateModal(true)}
          className={SETTINGS_BUTTON_PRIMARY}
        >
          <Plus className="w-4 h-4" aria-hidden="true" />
          Create invitation
        </ButtonBase>
      }
    >
      {loading ? (
        <SettingsBlock>
          <p className="text-sm contrast-helper">Loading invitations...</p>
        </SettingsBlock>
      ) : invitations.length === 0 ? (
        <SettingsBlock>
          <p className="text-sm contrast-helper">
            No invitations yet. Create one to get a sign-up link for a new
            person.
          </p>
        </SettingsBlock>
      ) : (
        invitations.map((inv) => {
          const role = inv.role.charAt(0).toUpperCase() + inv.role.slice(1);
          const expiresAt = parseServerTimestamp(inv.expires_at);
          const expires = inv.expires_at
            ? (expiresAt ? formatTimeZoneLocale(expiresAt, timeZone, "date") : "Unknown date")
            : null;
          return (
            <SettingsRow
              key={inv.id}
              label={`${role} invitation`}
              badge={
                inv.is_revoked ? (
                  <SettingsStatusBadge tone="error">Revoked</SettingsStatusBadge>
                ) : undefined
              }
              description={
                <>
                  <span className="block break-all font-mono">{inv.link}</span>
                  <span className="block">
                    Used {inv.used_count} of {inv.max_uses || "unlimited"}.{" "}
                    {expires ? `Expires ${expires}.` : "Never expires."}
                  </span>
                  {inv.users.length > 0 && (
                    <span className="block">Joined: {inv.users.join(", ")}</span>
                  )}
                </>
              }
              controlClassName="@min-[26rem]:min-w-0"
            >
              <div className="flex flex-wrap gap-2 @min-[26rem]:justify-end">
                {!inv.is_revoked && (
                  <ButtonBase
                    data-ui-fill="card"
                    data-ui-hover="inset"
                    data-ui-active="inset"
                    data-ui-border="full"
                    chrome="action"
                    type="button"
                    onClick={() => copyLink(inv.link)}
                    className={SETTINGS_BUTTON_SECONDARY}
                  >
                    <Copy className="h-4 w-4" aria-hidden="true" />
                    Copy link
                  </ButtonBase>
                )}
                {!inv.is_revoked ? (
                  <ButtonBase
                    data-ui-fill="card"
                    data-ui-hover="inset"
                    data-ui-active="inset"
                    data-ui-border="full"
                    chrome="action"
                    type="button"
                    onClick={() => handleRevokeClick(inv.id)}
                    className={SETTINGS_BUTTON_DANGER}
                  >
                    <XCircle className="h-4 w-4" aria-hidden="true" />
                    Revoke
                  </ButtonBase>
                ) : (
                  <ButtonBase
                    data-ui-fill="card"
                    data-ui-hover="inset"
                    data-ui-active="inset"
                    data-ui-border="full"
                    chrome="action"
                    type="button"
                    onClick={() => handleDeleteClick(inv.id)}
                    className={SETTINGS_BUTTON_DANGER}
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                    Delete
                  </ButtonBase>
                )}
              </div>
            </SettingsRow>
          );
        })
      )}

      {/* Create Modal */}
      <Modal
        open={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        size="sm"
        title="Create invitation"
        footer={
          <>
            <Button variant="ghost" onClick={() => setShowCreateModal(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={handleCreate}
              disabled={creating}
              loading={creating}
            >
              {creating ? "Creating..." : "Create invitation"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Select
            label="Role"
            value={role}
            onChange={(e) => setRole(e.target.value as UserRole)}
          >
            <option value={UserRole.USER}>User</option>
            <option value={UserRole.ADMIN}>Admin</option>
          </Select>

          <Input
            label="Expires after (days)"
            type="text"
            inputMode="numeric"
            value={expiresIn.toString()}
            onChange={(e) => {
              const val = sanitizeIntegerString(e.target.value, 1, 365);
              setExpiresIn(Number(val));
            }}
          />

          <Input
            label="Maximum uses"
            type="text"
            inputMode="numeric"
            value={maxUses.toString()}
            onChange={(e) => {
              const val = sanitizeIntegerString(e.target.value, 1, 100);
              setMaxUses(Number(val));
            }}
          />
        </div>
      </Modal>

      <ConfirmationModal
        isOpen={revokeModalOpen}
        onClose={() => setRevokeModalOpen(false)}
        onConfirm={confirmRevoke}
        title="Revoke invitation"
        message="Are you sure you want to revoke this invitation? The link will no longer be valid for new registrations."
        confirmText="Revoke"
        isDangerous={true}
      />

      <ConfirmationModal
        isOpen={deleteModalOpen}
        onClose={() => setDeleteModalOpen(false)}
        onConfirm={confirmDelete}
        title="Delete invitation"
        message="Are you sure you want to permanently delete this invitation? This cannot be undone."
        confirmText="Delete"
        isDangerous={true}
      />
    </SettingsCard>
  );
}
