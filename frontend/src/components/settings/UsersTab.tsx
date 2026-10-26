import { ButtonBase, InputBase, SelectBase } from "@/components/ui/nativeBases";
import { useState, useEffect } from "react";
import {
  getUsers,
  createUser,
  deleteUser,
  updateUser,
  updateUserRole,
} from "@/lib/api";
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
import { canGrantOwner, canManageAccount } from "@/lib/roles";
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

/** The roles a select offers; Owner only to an Owner, as the API allows. */
function RoleOptions({ includeOwner }: { includeOwner: boolean }) {
  return (
    <>
      <option value={UserRole.USER}>User</option>
      <option value={UserRole.ADMIN}>Admin</option>
      {includeOwner && <option value={UserRole.OWNER}>Owner</option>}
    </>
  );
}

/**
 * The users table. What it offers follows the API: any Owner or Admin may
 * create, delete and change the role of accounts (an Owner's only if they are
 * an Owner themselves), while editing an account's other details is for the
 * superuser alone, so everyone else gets a role-only dialog.
 */
export default function UsersTab() {
  const {
    userId: currentUserId,
    isSuperuser,
    role: viewerRole,
  } = useSettingsContext();
  const includeOwner = canGrantOwner(viewerRole);
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
      if (isSuperuser) {
        const updates: Partial<User> & { password?: string } = { ...editForm };
        if (updates.username) updates.username = trimString(updates.username);
        // Only send password if it's not empty
        if (!updates.password || updates.password.trim() === "") {
          delete updates.password;
        }
        // The API refuses a role on an Owner's account from anyone but an
        // Owner, even an unchanged one, so it is sent only when it changed.
        if (updates.role === editingUser.role) {
          delete updates.role;
        }
        await updateUser(editingUser.id, updates);
      } else if (editForm.role && editForm.role !== editingUser.role) {
        await updateUserRole(editingUser.id, editForm.role);
      }
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

  // A superuser who is not an Owner can edit an Owner's other details but not
  // their role.
  const roleLocked =
    editingUser !== null && !canManageAccount(viewerRole, editingUser);
  // The API refuses to deactivate the account making the request.
  const editingSelf = editingUser !== null && editingUser.id === currentUserId;

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
        <ButtonBase
          data-ui-fill="action"
          data-ui-hover="action-hover"
          data-ui-active="action-active"
          data-ui-border="full"
          chrome="action"
          type="button"
          onClick={toggleCreateForm}
          aria-expanded={isCreating}
          className={SETTINGS_BUTTON_PRIMARY}
        >
          <UserPlus className="h-4 w-4" aria-hidden="true" />
          Add user
        </ButtonBase>
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
              <InputBase
                data-ui-fill="control"
                data-ui-disabled-fill="control-disabled"
                data-ui-border="full"
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
              <InputBase
                data-ui-fill="control"
                data-ui-disabled-fill="control-disabled"
                data-ui-border="full"
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
              <SelectBase
                data-ui-fill="control"
                data-ui-disabled-fill="control-disabled"
                data-ui-border="full"
                id="new-user-role"
                value={newUser.role}
                onChange={(e) =>
                  setNewUser((prev) => ({ ...prev, role: e.target.value }))
                }
                className={SETTINGS_SELECT_CLASS}
              >
                <RoleOptions includeOwner={includeOwner} />
              </SelectBase>
            </div>
            <div className="flex justify-end gap-2 @min-[40rem]:col-span-3">
              <ButtonBase
                data-ui-fill="card"
                data-ui-hover="inset"
                data-ui-active="inset"
                data-ui-border="full"
                chrome="action"
                type="button"
                onClick={() => {
                  setIsCreating(false);
                  setNewUser({ ...EMPTY_NEW_USER });
                }}
                className={SETTINGS_BUTTON_SECONDARY}
              >
                Cancel
              </ButtonBase>
              <ButtonBase
                data-ui-fill="action"
                data-ui-hover="action-hover"
                data-ui-active="action-active"
                data-ui-border="full"
                chrome="action" type="submit" className={SETTINGS_BUTTON_PRIMARY}>
                Create user
              </ButtonBase>
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
          <InputBase
            data-ui-fill="control"
            data-ui-disabled-fill="control-disabled"
            data-ui-border="full"
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
          <tbody className="divide-y divide-surface-divider">
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
                const canManage = canManageAccount(viewerRole, user);
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
                        {(isSuperuser || canManage) && (
                          <ButtonBase
                            data-ui-fill="control"
                            data-ui-hover="inset"
                            data-ui-border="full"
                            chrome="action"
                            type="button"
                            onClick={() => startEdit(user)}
                            aria-label={
                              isSuperuser
                                ? `Edit ${user.username}`
                                : `Change the role of ${user.username}`
                            }
                            title={isSuperuser ? "Edit" : "Change role"}
                            className={ICON_BUTTON}
                          >
                            <Edit2 className="h-4 w-4" aria-hidden="true" />
                          </ButtonBase>
                        )}
                        {!isSelf && canManage && (
                          <ButtonBase
                            data-ui-fill="control"
                            data-ui-hover="inset"
                            data-ui-border="full"
                            chrome="action"
                            type="button"
                            onClick={() => handleDeleteUser(user.id)}
                            aria-label={`Delete ${user.username}`}
                            title="Delete"
                            className={cn(ICON_BUTTON, "text-danger-text")}
                          >
                            <Trash2 className="h-4 w-4" aria-hidden="true" />
                          </ButtonBase>
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
            <ButtonBase
              data-ui-fill="control"
              data-ui-hover="inset"
              data-ui-border="full"
              chrome="action"
              type="button"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              aria-label="Previous page"
              className={ICON_BUTTON}
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </ButtonBase>
            <ButtonBase
              data-ui-fill="control"
              data-ui-hover="inset"
              data-ui-border="full"
              chrome="action"
              type="button"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages}
              aria-label="Next page"
              className={ICON_BUTTON}
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </ButtonBase>
          </div>
        </div>
      </SettingsBlock>

      {/* Edit User Modal */}
      <Modal
        open={editModalOpen}
        onClose={() => setEditModalOpen(false)}
        size="sm"
        title={isSuperuser ? "Edit user" : "Change role"}
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditModalOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" form="edit-user-form" variant="primary">
              Save changes
            </Button>
          </>
        }
      >
        {isSuperuser ? (
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
              hint="Saving one signs them out everywhere and disconnects their connected apps, such as Claude and Codex."
            />

            <Select
              label="Role"
              value={editForm.role || "user"}
              onChange={(e) =>
                setEditForm({ ...editForm, role: e.target.value as UserRole })
              }
              disabled={roleLocked}
            >
              {roleLocked ? (
                <option value={UserRole.OWNER}>Owner</option>
              ) : (
                <RoleOptions includeOwner={includeOwner} />
              )}
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

            <div>
              <div className="flex items-center gap-2">
                <InputBase
                  type="checkbox"
                  id="is_active"
                  checked={editForm.is_active || false}
                  onChange={(e) =>
                    setEditForm({ ...editForm, is_active: e.target.checked })
                  }
                  disabled={editingSelf}
                  aria-describedby={editingSelf ? "is_active-message" : undefined}
                  className="rounded border-control-border accent-action focus-visible:outline-focus-ring disabled:cursor-not-allowed disabled:opacity-60"
                />
                <label
                  htmlFor="is_active"
                  className="text-sm font-medium text-contrast-muted"
                >
                  Active account
                </label>
              </div>
              {editingSelf && (
                <p id="is_active-message" className="mt-1.5 text-xs text-contrast-helper">
                  You cannot deactivate the account you are signed in with.
                </p>
              )}
            </div>
          </form>
        ) : (
          <form id="edit-user-form" onSubmit={saveEdit} className="space-y-4">
            <Select
              label="Role"
              value={editForm.role || "user"}
              onChange={(e) =>
                setEditForm({ ...editForm, role: e.target.value as UserRole })
              }
              hint="Only the account that set up this installation can change a username or password, deactivate an account or set its analysis limit."
            >
              <RoleOptions includeOwner={includeOwner} />
            </Select>
          </form>
        )}
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
