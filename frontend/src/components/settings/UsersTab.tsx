import { useState, useEffect } from 'react';
import { getUsers, createUser, deleteUser, updateUser } from '@/lib/api';
import { Loader2, Shield, Trash2, UserPlus, Edit2, X, Check, Search, ChevronLeft, ChevronRight } from 'lucide-react';
import { useNotificationStore } from '@/lib/notificationStore';
import ConfirmationModal from '../ConfirmationModal';
import { User } from '@/types';
import { trimString } from '@/lib/validation';

export default function UsersTab() {
    const [users, setUsers] = useState<User[]>([]);
    const [loading, setLoading] = useState(true);
    const { addNotification } = useNotificationStore();

    // Pagination & Search
    const [page, setPage] = useState(1);
    const [limit] = useState(10);
    const [total, setTotal] = useState(0);
    const [search, setSearch] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');

    // Create User State
    const [isCreating, setIsCreating] = useState(false);
    const [newUser, setNewUser] = useState({ username: '', password: '', role: 'user' });

    // Edit User State
    const [editModalOpen, setEditModalOpen] = useState(false);
    const [editingUser, setEditingUser] = useState<User | null>(null);
    const [editForm, setEditForm] = useState<Partial<User>>({});

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
            addNotification({ message: 'Failed to fetch users', type: 'error' });
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchUsers();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [page, debouncedSearch]);

    const handleCreateUser = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            const userToCreate = {
                ...newUser,
                username: trimString(newUser.username),
            };
            await createUser(userToCreate);
            addNotification({ message: 'User created successfully', type: 'success' });
            setIsCreating(false);
            setNewUser({ username: '', password: '', role: 'user' });
            fetchUsers();
        } catch (err: any) {
            addNotification({ message: err.response?.data?.detail || 'Failed to create user', type: 'error' });
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
            addNotification({ message: 'User deleted successfully', type: 'success' });
            setUsers(users.filter(u => u.id !== userToDelete));
        } catch (err: any) {
            addNotification({ message: err.response?.data?.detail || 'Failed to delete user', type: 'error' });
        } finally {
            setDeleteModalOpen(false);
            setUserToDelete(null);
        }
    };

    const startEdit = (user: User) => {
        setEditingUser(user);
        setEditForm({ username: user.username, role: user.role, is_active: user.is_active });
        setEditModalOpen(true);
    };

    const saveEdit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!editingUser) return;

        try {
            const updates = { ...editForm };
            if (updates.username) updates.username = trimString(updates.username);
            await updateUser(editingUser.id, updates);
            addNotification({ message: 'User updated successfully', type: 'success' });
            setEditModalOpen(false);
            setEditingUser(null);
            fetchUsers();
        } catch {
            addNotification({ message: 'Failed to update user', type: 'error' });
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <h3 className="text-lg font-medium text-gray-900 dark:text-white flex items-center gap-2">
                    <Shield className="w-5 h-5 text-purple-600 dark:text-purple-400" />
                    User Management
                </h3>
                <div className="flex items-center gap-4">
                    <div className="relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                        <input
                            value={search}
                            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
                            placeholder="Search users..."
                            className="pl-9 pr-4 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                        />
                    </div>
                    <button
                        onClick={() => setIsCreating(!isCreating)}
                        className="bg-purple-600 hover:bg-purple-700 text-white px-3 py-1.5 rounded text-sm flex items-center gap-2"
                    >
                        <UserPlus className="w-4 h-4" />
                        Add User
                    </button>
                </div>
            </div>

            {isCreating && (
                <div className="bg-white dark:bg-gray-800/50 p-4 rounded border border-purple-200 dark:border-purple-500/30 mb-4">
                    <h4 className="text-sm font-medium text-purple-700 dark:text-purple-300 mb-3">New User Details</h4>
                    <form onSubmit={handleCreateUser} className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <input
                            placeholder="Username"
                            value={newUser.username}
                            onChange={e => setNewUser({ ...newUser, username: e.target.value })}
                            className="bg-white dark:bg-gray-900 border border-gray-400 dark:border-gray-600 rounded px-3 py-2 text-sm text-gray-900 dark:text-white"
                            required
                        />
                        <input
                            placeholder="Password"
                            type="password"
                            value={newUser.password}
                            onChange={e => setNewUser({ ...newUser, password: e.target.value })}
                            className="bg-white dark:bg-gray-900 border border-gray-400 dark:border-gray-600 rounded px-3 py-2 text-sm text-gray-900 dark:text-white"
                            required
                        />
                        <select
                            value={newUser.role}
                            onChange={e => setNewUser({ ...newUser, role: e.target.value })}
                            className="bg-white dark:bg-gray-900 border border-gray-400 dark:border-gray-600 rounded px-3 py-2 text-sm text-gray-900 dark:text-white"
                        >
                            <option value="user">User</option>
                            <option value="admin">Admin</option>
                            <option value="owner">Owner</option>
                        </select>
                        <div className="md:col-span-2 flex justify-end gap-2">
                            <button type="button" onClick={() => setIsCreating(false)} className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-white text-sm px-3 py-1">Cancel</button>
                            <button type="submit" className="bg-purple-600 hover:bg-purple-700 text-white px-3 py-1 rounded text-sm">Create User</button>
                        </div>
                    </form>
                </div>
            )}

            <div className="bg-white dark:bg-gray-800/50 rounded-lg border border-gray-400 dark:border-gray-600 overflow-hidden">
                <table className="w-full text-left text-sm text-gray-700 dark:text-gray-400">
                    <thead className="bg-gray-100 dark:bg-gray-900/50 text-gray-700 dark:text-gray-200 uppercase font-medium">
                        <tr>
                            <th className="px-4 py-3">ID</th>
                            <th className="px-4 py-3">Username</th>
                            <th className="px-4 py-3">Role</th>
                            <th className="px-4 py-3">Status</th>
                            <th className="px-4 py-3 text-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-400 dark:divide-gray-600">
                        {loading ? (
                            <tr><td colSpan={5} className="p-4 text-center"><Loader2 className="w-5 h-5 animate-spin mx-auto" /></td></tr>
                        ) : users.map(user => (
                            <tr key={user.id} className="hover:bg-gray-100 dark:hover:bg-gray-700/30">
                                <td className="px-4 py-3">{user.id}</td>

                                {/* Username */}
                                <td className="px-4 py-3">
                                    {user.username}
                                </td>

                                {/* Role */}
                                <td className="px-4 py-3">
                                    <span className={`px-2 py-0.5 rounded text-xs ${user.role === 'owner' ? 'bg-orange-100 text-orange-800 dark:bg-orange-900/50 dark:text-orange-300' :
                                            user.role === 'admin' ? 'bg-purple-100 text-purple-800 dark:bg-purple-900/50 dark:text-purple-300' :
                                                'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300'
                                        }`}>
                                        {user.role.charAt(0).toUpperCase() + user.role.slice(1)}
                                    </span>
                                </td>

                                {/* Status */}
                                <td className="px-4 py-3">
                                    <span className={`px-2 py-0.5 rounded text-xs ${user.is_active ? 'bg-green-100 text-green-800 dark:bg-green-900/50 dark:text-green-300' : 'bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-300'}`}>
                                        {user.is_active ? 'Active' : 'Inactive'}
                                    </span>
                                </td>

                                {/* Actions */}
                                <td className="px-4 py-3 text-right flex justify-end gap-2">
                                    <button onClick={() => startEdit(user)} className="text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"><Edit2 className="w-4 h-4" /></button>
                                    <button onClick={() => handleDeleteUser(user.id)} className="text-red-600 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300"><Trash2 className="w-4 h-4" /></button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {/* Pagination */}
            <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 dark:border-gray-700">
                <div className="text-sm text-gray-500 dark:text-gray-400">
                    Showing {users.length > 0 ? (page - 1) * limit + 1 : 0} to {Math.min(page * limit, total)} of {total} users
                </div>
                <div className="flex gap-2">
                    <button
                        onClick={() => setPage(p => Math.max(1, p - 1))}
                        disabled={page === 1}
                        className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
                    >
                        <ChevronLeft className="w-5 h-5" />
                    </button>
                    <button
                        onClick={() => setPage(p => Math.min(Math.ceil(total / limit), p + 1))}
                        disabled={page >= Math.ceil(total / limit)}
                        className="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
                    >
                        <ChevronRight className="w-5 h-5" />
                    </button>
                </div>
            </div>

            {/* Edit User Modal */}
            {editModalOpen && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
                    <div className="bg-white dark:bg-gray-900 rounded-lg shadow-xl w-full max-w-md p-6 border border-gray-200 dark:border-gray-700">
                        <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">Edit User</h3>
                        <form onSubmit={saveEdit} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Username</label>
                                <input
                                    value={editForm.username || ''}
                                    onChange={e => setEditForm({ ...editForm, username: e.target.value })}
                                    className="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded px-3 py-2 text-sm text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                                />
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Role</label>
                                <select
                                    value={editForm.role || 'user'}
                                    onChange={e => setEditForm({ ...editForm, role: e.target.value as any })}
                                    className="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded px-3 py-2 text-sm text-gray-900 dark:text-white focus:ring-2 focus:ring-purple-500 focus:border-transparent"
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
                                    onChange={e => setEditForm({ ...editForm, is_active: e.target.checked })}
                                    className="rounded border-gray-300 text-purple-600 focus:ring-purple-500"
                                />
                                <label htmlFor="is_active" className="text-sm font-medium text-gray-700 dark:text-gray-300">Active Account</label>
                            </div>

                            <div className="flex justify-end gap-3 mt-6">
                                <button
                                    type="button"
                                    onClick={() => setEditModalOpen(false)}
                                    className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-md transition-colors"
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    className="px-4 py-2 text-sm font-medium text-white bg-purple-600 hover:bg-purple-700 rounded-md transition-colors"
                                >
                                    Save Changes
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            <ConfirmationModal
                isOpen={deleteModalOpen}
                onClose={() => setDeleteModalOpen(false)}
                onConfirm={confirmDelete}
                title="Delete User"
                message="Are you sure you want to delete this user? This action cannot be undone and will delete all data associated with this user."
                confirmText="Delete"
                isDangerous={true}
            />
        </div>
    );
}
