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
import Button from "../ui/Button";
import Input from "../ui/Input";
import Modal from "../ui/Modal";
import Select from "../ui/Select";
import { User, UserRole } from "@/types";
import { trimString } from "@/lib/validation";
import { cn } from "@/lib/cn";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import { useSettingsContext } from "./SettingsProvider";
import SettingsStatusBadge from "./SettingsStatusBadge";
import {
  SETTINGS_BUTTON_PRIMARY,
  SETTINGS_BUTTON_SECONDARY,
  SETTINGS_INPUT_CLASS,
  SETTINGS_SELECT_CLASS,
} from "./settingsControls";

const ICON_BUTTON =
  "inline-flex h-9 w-9 items-center justify-center rounded-lg border border-control-border bg-control-bg contrast-icon-muted transition-colors hover:bg-surface-inset hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring disabled:cursor-not-allowed disabled:opacity-60";

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
  const { userId: currentUserId } = useSettingsContext();
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
    // fetchUsers is redefined every render; the page and the debounced search
    // term are the only inputs that should start a fetch.
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
      ai_analysis_daily_limit: user.ai_analysis_daily_limit ?? null,
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

  const roleLabel = (role: string) => role.charAt(0).toUpperCase() + role.slice(1);
  const roleTone = (role: string) =>
    role === "owner" ? "warning" : role === "admin" ? "info" : "neutral";
  const totalPages = Math.max(1, Math.ceil(total / limit));

  return (
    <SettingsCard
      id="users-accounts"
      title="Users"
      description="Each person's role and whether their account is active."
      headerAside={
        <button
          type="button"
          onClick={toggleCreateForm}
          aria-expanded={isCreating}
          className={SETTINGS_BUTTON_PRIMARY}
        >
          <UserPlus className="h-4 w-4" aria-hidden="true" />
          Add user
        </button>
      }
    >
      {isCreating && (
        <SettingsBlock label="New user" description="They choose a new password the first time they sign in.">
          <form
            onSubmit={handleCreateUser}
            autoComplete="off"
            className="grid grid-cols-1 gap-4 @min-[40rem]:grid-cols-3"
          >
            <div>
              <label htmlFor="new-user-username" className="mb-1 block text-sm font-medium text-foreground">
                Username
              </label>
              <input
                id="new-user-username"
                name="new-user-username"
                value={newUser.username}
                onChange={(e) =>
                  setNewUser((prev) => ({ ...prev, username: e.target.value }))
                }
                autoComplete="off"
                className={SETTINGS_INPUT_CLASS}
                required
              />
            </div>
            <div>
              <label htmlFor="new-user-password" className="mb-1 block text-sm font-medium text-foreground">
                Temporary password
              </label>
              <input
                id="new-user-password"
                name="new-user-password"
                type="password"
                value={newUser.password}
                onChange={(e) =>
                  setNewUser((prev) => ({ ...prev, password: e.target.value }))
                }
                autoComplete="new-password"
                minLength={8}
                className={SETTINGS_INPUT_CLASS}
                required
              />
            </div>
            <div>
              <label htmlFor="new-user-role" className="mb-1 block text-sm font-medium text-foreground">
                Role
              </label>
              <select
                id="new-user-role"
                value={newUser.role}
                onChange={(e) =>
                  setNewUser((prev) => ({ ...prev, role: e.target.value }))
                }
                className={SETTINGS_SELECT_CLASS}
              >
                <option value="user">User</option>
                <option value="admin">Admin</option>
                <option value="owner">Owner</option>
              </select>
            </div>
            <div className="flex justify-end gap-2 @min-[40rem]:col-span-3">
              <button
                type="button"
                onClick={() => {
                  setIsCreating(false);
                  setNewUser({ ...EMPTY_NEW_USER });
                }}
                className={SETTINGS_BUTTON_SECONDARY}
              >
                Cancel
              </button>
              <button type="submit" className={SETTINGS_BUTTON_PRIMARY}>
                Create user
              </button>
            </div>
          </form>
        </SettingsBlock>
      )}

      <SettingsBlock>
        <div className="relative @min-[32rem]:max-w-xs">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 contrast-icon-muted"
            aria-hidden="true"
          />
          <input
            type="search"
            aria-label="Search users"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            placeholder="Search users"
            className={cn(SETTINGS_INPUT_CLASS, "pl-9")}
          />
        </div>
      </SettingsBlock>

      <SettingsBlock>
        <table className="settings-table w-full text-left text-sm text-foreground">
          <thead className="border-b settings-divider text-xs font-medium contrast-helper">
            <tr>
              <th className="py-2 pr-4 font-medium">Username</th>
              <th className="py-2 pr-4 font-medium">Role</th>
              <th className="py-2 pr-4 font-medium">Status</th>
              <th className="py-2 text-right font-medium">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y settings-divider">
            {loading ? (
              <tr>
                <td colSpan={4} className="py-6 text-center">
                  <Loader2
                    className="mx-auto h-5 w-5 animate-spin contrast-icon-muted"
                    aria-label="Loading"
                  />
                </td>
              </tr>
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-6 text-center contrast-helper">
                  No users match that search.
                </td>
              </tr>
            ) : (
              users.map((user) => {
                const isSelf = user.id === currentUserId;
                return (
                  <tr key={user.id}>
                    <td className="py-3 pr-4 font-medium">
                      {user.username}
                      {isSelf && (
                        <span className="ml-2 text-xs font-normal contrast-helper">
                          You
                        </span>
                      )}
                    </td>
                    <td data-label="Role" className="py-3 pr-4">
                      <SettingsStatusBadge tone={roleTone(user.role)}>
                        {roleLabel(user.role)}
                      </SettingsStatusBadge>
                    </td>
                    <td data-label="Status" className="py-3 pr-4">
                      <SettingsStatusBadge tone={user.is_active ? "success" : "error"}>
                        {user.is_active ? "Active" : "Inactive"}
                      </SettingsStatusBadge>
                    </td>
                    <td className="py-3">
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => startEdit(user)}
                          aria-label={`Edit ${user.username}`}
                          title="Edit"
                          className={ICON_BUTTON}
                        >
                          <Edit2 className="h-4 w-4" aria-hidden="true" />
                        </button>
                        {!isSelf && (
                          <button
                            type="button"
                            onClick={() => handleDeleteUser(user.id)}
                            aria-label={`Delete ${user.username}`}
                            title="Delete"
                            className={cn(ICON_BUTTON, "text-danger-text")}
                          >
                            <Trash2 className="h-4 w-4" aria-hidden="true" />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>

        <div className="mt-3 flex items-center justify-between gap-3 border-t pt-3 settings-divider">
          <p className="text-sm contrast-helper">
            {total === 0
              ? "No users"
              : `${(page - 1) * limit + 1} to ${Math.min(page * limit, total)} of ${total}`}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              aria-label="Previous page"
              className={ICON_BUTTON}
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              aria-label="Next page"
              className={ICON_BUTTON}
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      </SettingsBlock>

      {/* Edit User Modal */}
      <Modal
        open={editModalOpen}
        onClose={() => setEditModalOpen(false)}
        size="sm"
        title="Edit user"
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditModalOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" form="edit-user-form" variant="primary">
              Save Changes
            </Button>
          </>
        }
      >
        <form id="edit-user-form" onSubmit={saveEdit} className="space-y-4">
          <Input
            label="Username"
            value={editForm.username || ""}
            onChange={(e) => setEditForm({ ...editForm, username: e.target.value })}
          />

          <Input
            label={
              <>
                New Password{" "}
                <span className="font-normal text-contrast-helper">
                  (Leave blank to keep current)
                </span>
              </>
            }
            name="edit-user-password"
            type="password"
            value={editForm.password || ""}
            onChange={(e) =>
              setEditForm((prev) => ({ ...prev, password: e.target.value }))
            }
            placeholder="Enter new password"
            autoComplete="new-password"
            minLength={8}
          />

          <Select
            label="Role"
            value={editForm.role || "user"}
            onChange={(e) =>
              setEditForm({ ...editForm, role: e.target.value as UserRole })
            }
          >
            <option value="user">User</option>
            <option value="admin">Admin</option>
            <option value="owner">Owner</option>
          </Select>

          <Input
            label={
              <>
                Daily AI analysis limit{" "}
                <span className="font-normal text-contrast-helper">
                  (blank for the installation default, 0 for no cap)
                </span>
              </>
            }
            type="number"
            min={0}
            step={1}
            value={editForm.ai_analysis_daily_limit ?? ""}
            onChange={(e) =>
              setEditForm({
                ...editForm,
                ai_analysis_daily_limit:
                  e.target.value === "" ? null : Number(e.target.value),
              })
            }
          />

          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="is_active"
              checked={editForm.is_active || false}
              onChange={(e) =>
                setEditForm({ ...editForm, is_active: e.target.checked })
              }
              className="rounded border-control-border accent-action focus-visible:outline-focus-ring"
            />
            <label
              htmlFor="is_active"
              className="text-sm font-medium text-contrast-muted"
            >
              Active account
            </label>
          </div>
        </form>
      </Modal>

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
