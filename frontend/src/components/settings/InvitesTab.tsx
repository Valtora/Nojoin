'use client';

import { useState, useEffect } from 'react';
import { Invitation, UserRole } from '@/types';
import { getInvitations, createInvitation, revokeInvitation, deleteInvitation } from '@/lib/api';
import { sanitizeIntegerString } from '@/lib/validation';
import { Plus, Trash2, Copy, Check, Users, Clock, Shield, XCircle } from 'lucide-react';
import ConfirmationModal from '../ConfirmationModal';

export default function InvitesTab() {
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreateModal, setShowCreateModal] = useState(false);
  
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

  const fetchInvitations = async () => {
    try {
      const data = await getInvitations();
      setInvitations(data);
    } catch (e) {
      console.error("Failed to fetch invitations", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchInvitations();
  }, []);

  const handleCreate = async () => {
    setCreating(true);
    try {
      await createInvitation(role, expiresIn, maxUses);
      await fetchInvitations();
      setShowCreateModal(false);
    } catch (e) {
      console.error("Failed to create invitation", e);
      alert("Failed to create invitation");
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
    } catch (e) {
      console.error("Failed to revoke invitation", e);
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
    } catch (e) {
      console.error("Failed to delete invitation", e);
    } finally {
      setDeleteModalOpen(false);
      setInviteToDelete(null);
    }
  };

  const copyLink = (link: string) => {
    navigator.clipboard.writeText(link);
    // Could add toast here
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-medium text-gray-900 dark:text-white">Invitation Management</h3>
        <button
          onClick={() => setShowCreateModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-orange-600 text-white rounded-md hover:bg-orange-700 transition-colors"
        >
          <Plus className="w-4 h-4" />
          Create Invite
        </button>
      </div>

      {loading ? (
        <div className="text-center py-8 text-gray-500">Loading invitations...</div>
      ) : invitations.length === 0 ? (
        <div className="text-center py-8 text-gray-500 bg-gray-50 dark:bg-gray-800/50 rounded-lg border border-gray-200 dark:border-gray-700">
          No invitations found. Create one to get started.
        </div>
      ) : (
        <div className="grid gap-4 grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
          {invitations.map((inv) => (
            <div key={inv.id} className={`p-4 rounded-lg border ${inv.is_revoked ? 'bg-gray-100 border-gray-200 dark:bg-gray-900 dark:border-gray-800 opacity-75' : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 shadow-sm'}`}>
              <div className="flex justify-between items-start mb-3">
                <div className="flex items-center gap-2">
                    <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${
                        inv.role === UserRole.ADMIN ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400' : 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                    }`}>
                        {inv.role}
                    </span>
                    {inv.is_revoked && <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400">Revoked</span>}
                </div>
                {!inv.is_revoked ? (
                    <button onClick={() => handleRevokeClick(inv.id)} className="text-gray-400 hover:text-red-500 transition-colors" title="Revoke">
                        <XCircle className="w-4 h-4" />
                    </button>
                ) : (
                    <button onClick={() => handleDeleteClick(inv.id)} className="text-gray-400 hover:text-red-500 transition-colors" title="Delete">
                        <Trash2 className="w-4 h-4" />
                    </button>
                )}
              </div>
              
              <div className="flex items-center gap-2 mb-4 bg-gray-50 dark:bg-gray-900 p-2 rounded border border-gray-200 dark:border-gray-700">
                <input 
                    readOnly 
                    value={inv.link} 
                    className="flex-1 bg-transparent text-sm text-gray-600 dark:text-gray-300 outline-none truncate"
                />
                <button onClick={() => copyLink(inv.link)} className="text-gray-500 hover:text-orange-600">
                    <Copy className="w-4 h-4" />
                </button>
              </div>

              <div className="space-y-2 text-sm text-gray-500 dark:text-gray-400">
                <div className="flex items-center gap-2">
                    <Users className="w-4 h-4" />
                    <span>Used: {inv.used_count} / {inv.max_uses || '∞'}</span>
                </div>
                <div className="flex items-center gap-2">
                    <Clock className="w-4 h-4" />
                    <span>Expires: {inv.expires_at ? new Date(inv.expires_at).toLocaleDateString() : 'Never'}</span>
                </div>
              </div>
              
              {inv.users.length > 0 && (
                  <div className="mt-4 pt-3 border-t border-gray-100 dark:border-gray-700">
                      <p className="text-xs font-medium text-gray-500 mb-1">Joined Users:</p>
                      <div className="flex flex-wrap gap-1">
                          {inv.users.map(u => (
                              <span key={u} className="text-xs px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-gray-600 dark:text-gray-300">
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
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
          <div className="bg-white dark:bg-gray-900 rounded-lg shadow-xl w-full max-w-md p-6 border border-gray-200 dark:border-gray-700">
            <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Create Invitation</h3>
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Role</label>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value as UserRole)}
                  className="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded px-3 py-2 text-sm text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                >
                  <option value={UserRole.USER}>User</option>
                  <option value={UserRole.ADMIN}>Admin</option>
                </select>
              </div>
              
              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Expires In (Days)</label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={expiresIn.toString()}
                  onChange={(e) => {
                      const val = sanitizeIntegerString(e.target.value, 1, 365);
                      setExpiresIn(Number(val));
                  }}
                  className="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded px-3 py-2 text-sm text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Max Uses</label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={maxUses.toString()}
                  onChange={(e) => {
                      const val = sanitizeIntegerString(e.target.value, 1, 100);
                      setMaxUses(Number(val));
                  }}
                  className="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded px-3 py-2 text-sm text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                />
              </div>

              <div className="flex justify-end gap-3 mt-6">
                <button 
                  onClick={() => setShowCreateModal(false)}
                  className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-md transition-colors"
                >
                  Cancel
                </button>
                <button 
                  onClick={handleCreate}
                  disabled={creating}
                  className="px-4 py-2 text-sm font-medium text-white bg-orange-600 hover:bg-orange-700 rounded-md transition-colors disabled:opacity-50"
                >
                  {creating ? 'Creating...' : 'Create Invite'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

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
        title="Delete Invitation"
        message="Are you sure you want to permanently delete this invitation? This action cannot be undone."
        confirmText="Delete"
        isDangerous={true}
      />
    </div>
  );
}
