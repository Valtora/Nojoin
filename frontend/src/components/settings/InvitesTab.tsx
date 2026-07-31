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
      {showCreateModal && (
        <div className="fixed inset-0 z-[var(--z-modal)] flex items-center justify-center bg-scrim p-4">
          <div className="bg-surface-card rounded-lg shadow-xl w-full max-w-md max-h-[calc(100dvh-2rem)] overflow-y-auto p-6 border border-surface-border">
            <h3 className="text-lg font-medium text-foreground mb-4">
              Create Invitation
            </h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-contrast-muted mb-1">
                  Role
                </label>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value as UserRole)}
                  className="w-full bg-surface-card border border-control-border rounded px-3 py-2 text-sm text-foreground focus:ring-2 focus-visible:outline-focus-ring focus:border-transparent"
                >
                  <option value={UserRole.USER}>User</option>
                  <option value={UserRole.ADMIN}>Admin</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-contrast-muted mb-1">
                  Expires In (Days)
                </label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={expiresIn.toString()}
                  onChange={(e) => {
                    const val = sanitizeIntegerString(e.target.value, 1, 365);
                    setExpiresIn(Number(val));
                  }}
                  className="w-full bg-surface-card border border-control-border rounded px-3 py-2 text-sm text-foreground focus:ring-2 focus-visible:outline-focus-ring focus:border-transparent"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-contrast-muted mb-1">
                  Max Uses
                </label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={maxUses.toString()}
                  onChange={(e) => {
                    const val = sanitizeIntegerString(e.target.value, 1, 100);
                    setMaxUses(Number(val));
                  }}
                  className="w-full bg-surface-card border border-control-border rounded px-3 py-2 text-sm text-foreground focus:ring-2 focus-visible:outline-focus-ring focus:border-transparent"
                />
              </div>

              <div className="flex justify-end gap-3 mt-6">
                <button
                  onClick={() => setShowCreateModal(false)}
                  className="px-4 py-2 text-sm font-medium text-contrast-muted hover:bg-surface-inset rounded-md transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleCreate}
                  disabled={creating}
                  className="px-4 py-2 text-sm font-medium text-action-on bg-action hover:bg-action-hover rounded-md transition-colors disabled:opacity-50"
                >
                  {creating ? "Creating..." : "Create Invite"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

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
