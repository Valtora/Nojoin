"use client";

import { useState, useEffect, useCallback } from "react";
import { Invitation, UserRole } from "@/types";
import {
  getInvitations,
  createInvitation,
  revokeInvitation,
  deleteInvitation,
} from "@/lib/api";
import { sanitizeIntegerString } from "@/lib/validation";
import { Plus, Trash2, Copy, Users, Clock, XCircle } from "lucide-react";
import ConfirmationModal from "../ConfirmationModal";
import Button from "../ui/Button";
import Input from "../ui/Input";
import Modal from "../ui/Modal";
import Select from "../ui/Select";
import { useNotificationStore } from "@/lib/notificationStore";
import SettingsCallout from "./SettingsCallout";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import { SETTINGS_BUTTON_PRIMARY } from "./settingsControls";

export default function InvitesTab() {
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const { addNotification } = useNotificationStore();

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
      description="Invitation links for new sign-ups. Each can be revoked before it is used."
      headerAside={
        <button
          onClick={() => setShowCreateModal(true)}
          className={SETTINGS_BUTTON_PRIMARY}
        >
          <Plus className="w-4 h-4" aria-hidden="true" />
          Create invite
        </button>
      }
    >
      <SettingsBlock contentClassName="space-y-6">

      {loading ? (
        <SettingsCallout tone="neutral" message="Loading invitations..." />
      ) : invitations.length === 0 ? (
        <SettingsCallout
          tone="neutral"
          title="No invitations yet"
          message="Create an invitation to generate a registration link for a new user."
        />
      ) : (
        <div className="grid gap-4 grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
          {invitations.map((inv) => (
            <div
              key={inv.id}
              className={`settings-inset rounded-xl p-4 ${inv.is_revoked ? "opacity-75" : ""}`}
            >
              <div className="flex justify-between items-start mb-3">
                <div className="flex items-center gap-2">
                  <span
                    className={`px-2 py-0.5 text-xs font-medium rounded-full ${
                      inv.role === UserRole.ADMIN
                        ? "bg-status-info-bg text-status-info-fg"
                        : "bg-status-info-bg text-status-info-fg"
                    }`}
                  >
                    {inv.role}
                  </span>
                  {inv.is_revoked && (
                    <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-status-danger-bg text-status-danger-fg">
                      Revoked
                    </span>
                  )}
                </div>
                {!inv.is_revoked ? (
                  <button
                    onClick={() => handleRevokeClick(inv.id)}
                    className="text-contrast-helper hover:text-danger-text transition-colors"
                    title="Revoke"
                  >
                    <XCircle className="w-4 h-4" />
                  </button>
                ) : (
                  <button
                    onClick={() => handleDeleteClick(inv.id)}
                    className="text-contrast-helper hover:text-danger-text transition-colors"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>

              <div className="flex items-center gap-2 mb-4 rounded-2xl border border-surface-border bg-surface-inset p-2">
                <input
                  readOnly
                  value={inv.link}
                  className="flex-1 bg-transparent text-sm text-contrast-helper outline-none truncate"
                />
                <button
                  onClick={() => copyLink(inv.link)}
                  className="text-contrast-helper hover:text-action-text"
                >
                  <Copy className="w-4 h-4" />
                </button>
              </div>

              <div className="space-y-2 text-sm contrast-helper">
                <div className="flex items-center gap-2">
                  <Users className="w-4 h-4" />
                  <span>
                    Used: {inv.used_count} / {inv.max_uses || "∞"}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Clock className="w-4 h-4" />
                  <span>
                    Expires:{" "}
                    {inv.expires_at
                      ? new Date(inv.expires_at).toLocaleDateString()
                      : "Never"}
                  </span>
                </div>
              </div>

              {inv.users.length > 0 && (
                <div className="mt-4 pt-3 border-t border-surface-border">
                  <p className="text-xs font-medium contrast-helper mb-1">
                    Joined Users:
                  </p>
                  <div className="flex flex-wrap gap-1">
                    {inv.users.map((u) => (
                      <span
                        key={u}
                        className="text-xs px-1.5 py-0.5 bg-surface-inset rounded text-contrast-helper"
                      >
                        {u}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Create Modal */}
      <Modal
        open={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        size="sm"
        title="Create Invitation"
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
              {creating ? "Creating..." : "Create Invite"}
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
            label="Expires In (Days)"
            type="text"
            inputMode="numeric"
            value={expiresIn.toString()}
            onChange={(e) => {
              const val = sanitizeIntegerString(e.target.value, 1, 365);
              setExpiresIn(Number(val));
            }}
          />

          <Input
            label="Max Uses"
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

      </SettingsBlock>

      <ConfirmationModal
        isOpen={revokeModalOpen}
        onClose={() => setRevokeModalOpen(false)}
        onConfirm={confirmRevoke}
        title="Revoke Invitation"
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
