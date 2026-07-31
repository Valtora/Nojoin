import { useState, useEffect } from "react";
import { getUsers, createUser, deleteUser, updateUser } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import {
  Loader2,
  Trash2,
  UserPlus,
  Edit2,
  Search,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { useNotificationStore } from "@/lib/notificationStore";
import ConfirmationModal from "../ConfirmationModal";
import { User, UserRole } from "@/types";
import { trimString } from "@/lib/validation";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";

type NewUserFormState = {
  username: string;
  password: string;
  role: string;
};

const EMPTY_NEW_USER: NewUserFormState = {
  username: "",
  password: "",
  role: "user",
};

export default function UsersTab() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const { addNotification } = useNotificationStore();

  // Pagination & Search
  const [page, setPage] = useState(1);
  const [limit] = useState(10);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  // Create User State
  const [isCreating, setIsCreating] = useState(false);
  const [newUser, setNewUser] = useState({ ...EMPTY_NEW_USER });

  // Edit User State
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [editForm, setEditForm] = useState<
    Partial<User> & { password?: string }
  >({});

  // Delete User State
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [userToDelete, setUserToDelete] = useState<number | null>(null);

  // Debounce search
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 500);
    return () => clearTimeout(timer);
  }, [search]);

  const fetchUsers = async () => {
    setLoading(true);
    try {
      const skip = (page - 1) * limit;
      const data = await getUsers(skip, limit, debouncedSearch);
      setUsers(data.items);
      setTotal(data.total);
    } catch {
      addNotification({ message: "Failed to fetch users", type: "error" });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, debouncedSearch]);

  const toggleCreateForm = () => {
    setIsCreating((prev) => {
      const next = !prev;
      if (next) {
        setNewUser({ ...EMPTY_NEW_USER });
      }
      return next;
    });
  };

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const userToCreate = {
        ...newUser,
        username: trimString(newUser.username),
      };
      await createUser(userToCreate);
      addNotification({
        message: "User created successfully",
        type: "success",
      });
      setIsCreating(false);
      setNewUser({ ...EMPTY_NEW_USER });
      await fetchUsers();
    } catch (err: unknown) {
      addNotification({
        message: getErrorMessage(err, "Failed to create user"),
        type: "error",
      });
    }
  };

  const handleDeleteUser = (id: number) => {
    setUserToDelete(id);
    setDeleteModalOpen(true);
  };

  const confirmDelete = async () => {
    if (!userToDelete) return;
    try {
      await deleteUser(userToDelete);
      addNotification({
        message: "User deleted successfully",
        type: "success",
      });
      const nextTotal = Math.max(0, total - 1);
      const nextPage = Math.min(
        page,
        Math.max(1, Math.ceil(nextTotal / limit)),
      );

      if (nextPage !== page) {
        setPage(nextPage);
      } else {
        await fetchUsers();
      }
    } catch (err: unknown) {
      addNotification({
        message: getErrorMessage(err, "Failed to delete user"),
        type: "error",
      });
    } finally {
      setDeleteModalOpen(false);
      setUserToDelete(null);
    }
  };

  const startEdit = (user: User) => {
    setEditingUser(user);
    setEditForm({
      username: user.username,
      role: user.role,
      is_active: user.is_active,
      password: "",
    });
    setEditModalOpen(true);
  };

  const saveEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingUser) return;

    try {
      const updates: Partial<User> & { password?: string } = { ...editForm };
      if (updates.username) updates.username = trimString(updates.username);
      // Only send password if it's not empty
      if (!updates.password || updates.password.trim() === "") {
        delete updates.password;
      }
      await updateUser(editingUser.id, updates);
      addNotification({
        message: "User updated successfully",
        type: "success",
      });
      setEditModalOpen(false);
      setEditingUser(null);
      await fetchUsers();
    } catch (err: unknown) {
      addNotification({
        message: getErrorMessage(err, "Failed to update user"),
        type: "error",
      });
    }
  };

  return (
    <SettingsCard
      id="users-accounts"
      title="Users"
      description="Accounts on this installation, their roles, and the access each one has."
    >
      <SettingsBlock contentClassName="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative w-full sm:max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-contrast-helper" />
          <input
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            placeholder="Search users..."
            className="w-full pl-9 pr-4 py-2 text-sm border border-control-border rounded-xl bg-surface-card text-foreground focus:ring-2 focus-visible:outline-focus-ring focus:border-transparent"
          />
        </div>
        <button
          onClick={toggleCreateForm}
          className="inline-flex items-center justify-center gap-2 rounded-xl bg-action px-4 py-2 text-sm font-semibold text-white transition hover:bg-action-hover"
        >
          <UserPlus className="w-4 h-4" />
          Add User
        </button>
      </div>

      {isCreating && (
        <div className="settings-inset rounded-xl p-4 space-y-4">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-contrast-helper">
              Create user
            </div>
            <h4 className="mt-2 text-base font-semibold text-foreground">
              New account
            </h4>
          </div>
          <form
            onSubmit={handleCreateUser}
            autoComplete="off"
            className="grid grid-cols-1 md:grid-cols-2 gap-4"
          >
            <input
              name="new-user-username"
              placeholder="Username"
              value={newUser.username}
              onChange={(e) =>
                setNewUser((prev) => ({ ...prev, username: e.target.value }))
              }
              autoComplete="off"
              className="bg-surface-card border border-gray-400 dark:border-gray-600 rounded px-3 py-2 text-sm text-foreground"
              required
            />
            <input
              name="new-user-password"
              placeholder="Password"
              type="password"
              value={newUser.password}
              onChange={(e) =>
                setNewUser((prev) => ({ ...prev, password: e.target.value }))
              }
              autoComplete="new-password"
              minLength={8}
              className="bg-surface-card border border-gray-400 dark:border-gray-600 rounded px-3 py-2 text-sm text-foreground"
              required
            />
            <select
              value={newUser.role}
              onChange={(e) =>
                setNewUser((prev) => ({ ...prev, role: e.target.value }))
              }
              className="bg-surface-card border border-gray-400 dark:border-gray-600 rounded px-3 py-2 text-sm text-foreground"
            >
              <option value="user">User</option>
              <option value="admin">Admin</option>
              <option value="owner">Owner</option>
            </select>
            <div className="md:col-span-2 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setIsCreating(false);
                  setNewUser({ ...EMPTY_NEW_USER });
                }}
                className="px-3 py-1 text-sm contrast-helper hover:text-foreground dark:hover:text-white"
              >
                Cancel
              </button>
              <button
                type="submit"
                className="rounded-xl bg-action px-4 py-2 text-sm font-semibold text-white transition hover:bg-action-hover"
              >
                Create User
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="settings-inset rounded-xl p-4 overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm text-gray-800 dark:text-gray-200 whitespace-nowrap">
            <thead className="bg-surface-inset/80 text-foreground uppercase font-medium">
              <tr>
                <th className="px-4 py-3">ID</th>
                <th className="px-4 py-3">Username</th>
                <th className="px-4 py-3">Role</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-300 dark:divide-gray-600">
              {loading ? (
                <tr>
                  <td colSpan={5} className="p-4 text-center">
                    <Loader2 className="w-5 h-5 animate-spin mx-auto" />
                  </td>
                </tr>
              ) : users.length === 0 ? (
                <tr>
                  <td
                    colSpan={5}
                    className="p-8 text-center text-sm text-contrast-helper"
                  >
                    No users found.
                  </td>
                </tr>
              ) : (
                users.map((user) => (
                  <tr
                    key={user.id}
                    className="hover:bg-surface-inset/40"
                  >
                    <td className="px-4 py-3">{user.id}</td>

                    {/* Username */}
                    <td className="px-4 py-3">{user.username}</td>

                    {/* Role */}
                    <td className="px-4 py-3">
                      <span
                        className={`px-2 py-0.5 rounded text-xs ${
                          user.role === "owner"
                            ? "bg-orange-100 text-orange-800 dark:bg-orange-900/50 dark:text-orange-300"
                            : user.role === "admin"
                              ? "bg-purple-100 text-purple-800 dark:bg-purple-900/50 dark:text-purple-300"
                              : "bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300"
                        }`}
                      >
                        {user.role.charAt(0).toUpperCase() + user.role.slice(1)}
                      </span>
                    </td>

                    {/* Status */}
                    <td className="px-4 py-3">
                      <span
                        className={`px-2 py-0.5 rounded text-xs ${user.is_active ? "bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300" : "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300"}`}
                      >
                        {user.is_active ? "Active" : "Inactive"}
                      </span>
                    </td>

                    {/* Actions */}
                    <td className="px-4 py-3 text-right flex justify-end gap-2">
                      <button
                        onClick={() => startEdit(user)}
                        className="text-blue-600 hover:text-status-info-fg dark:hover:text-blue-300"
                      >
                        <Edit2 className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => handleDeleteUser(user.id)}
                        className="text-danger-text hover:text-status-danger-fg dark:hover:text-red-300"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-surface-border px-4 py-3">
          <div className="text-sm contrast-helper">
            Showing {users.length > 0 ? (page - 1) * limit + 1 : 0} to{" "}
            {Math.min(page * limit, total)} of {total} users
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="p-1 rounded text-contrast-muted hover:bg-surface-inset disabled:opacity-50"
            >
              <ChevronLeft className="w-5 h-5" />
            </button>
            <button
              onClick={() =>
                setPage((p) => Math.min(Math.ceil(total / limit), p + 1))
              }
              disabled={page >= Math.ceil(total / limit)}
              className="p-1 rounded text-contrast-muted hover:bg-surface-inset disabled:opacity-50"
            >
              <ChevronRight className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>

      {/* Edit User Modal */}
      {editModalOpen && (
        <div className="fixed inset-0 z-[var(--z-modal)] flex items-center justify-center bg-scrim p-4">
          <div className="bg-surface-card rounded-lg shadow-xl w-full max-w-md max-h-[calc(100dvh-2rem)] overflow-y-auto p-6 border border-surface-border">
            <h3 className="text-lg font-medium text-foreground mb-4">
              Edit User
            </h3>
            <form onSubmit={saveEdit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-contrast-muted mb-1">
                  Username
                </label>
                <input
                  value={editForm.username || ""}
                  onChange={(e) =>
                    setEditForm({ ...editForm, username: e.target.value })
                  }
                  className="w-full bg-surface-card border border-control-border rounded px-3 py-2 text-sm text-foreground focus:ring-2 focus-visible:outline-focus-ring focus:border-transparent"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-contrast-muted mb-1">
                  New Password{" "}
                  <span className="contrast-helper font-normal">
                    (Leave blank to keep current)
                  </span>
                </label>
                <input
                  name="edit-user-password"
                  type="password"
                  value={editForm.password || ""}
                  onChange={(e) =>
                    setEditForm((prev) => ({
                      ...prev,
                      password: e.target.value,
                    }))
                  }
                  placeholder="Enter new password"
                  autoComplete="new-password"
                  minLength={8}
                  className="w-full bg-surface-card border border-control-border rounded px-3 py-2 text-sm text-foreground focus:ring-2 focus-visible:outline-focus-ring focus:border-transparent"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-contrast-muted mb-1">
                  Role
                </label>
                <select
                  value={editForm.role || "user"}
                  onChange={(e) =>
                    setEditForm({
                      ...editForm,
                      role: e.target.value as UserRole,
                    })
                  }
                  className="w-full bg-surface-card border border-control-border rounded px-3 py-2 text-sm text-foreground focus:ring-2 focus-visible:outline-focus-ring focus:border-transparent"
                >
                  <option value="user">User</option>
                  <option value="admin">Admin</option>
                  <option value="owner">Owner</option>
                </select>
              </div>

              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="is_active"
                  checked={editForm.is_active || false}
                  onChange={(e) =>
                    setEditForm({ ...editForm, is_active: e.target.checked })
                  }
                  className="rounded border-gray-300 text-action-text focus-visible:outline-focus-ring"
                />
                <label
                  htmlFor="is_active"
                  className="text-sm font-medium text-contrast-muted"
                >
                  Active Account
                </label>
              </div>

              <div className="flex justify-end gap-3 mt-6">
                <button
                  type="button"
                  onClick={() => setEditModalOpen(false)}
                  className="px-4 py-2 text-sm font-medium text-contrast-muted hover:bg-surface-inset rounded-md transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 text-sm font-medium text-white bg-action hover:bg-action-hover rounded-md transition-colors"
                >
                  Save Changes
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      </SettingsBlock>

      <ConfirmationModal
        isOpen={deleteModalOpen}
        onClose={() => setDeleteModalOpen(false)}
        onConfirm={confirmDelete}
        title="Delete user"
        message="Are you sure you want to delete this user? This cannot be undone and deletes all data associated with the account."
        confirmText="Delete"
        isDangerous={true}
      />
    </SettingsCard>
  );
}
