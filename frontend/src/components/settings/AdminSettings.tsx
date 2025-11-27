import { useState, useEffect } from 'react';
import { getUsers, createUser, deleteUser, updateUser } from '@/lib/api';
import { Loader2, Shield, Trash2, UserPlus, Edit2, X, Check } from 'lucide-react';
import { useNotificationStore } from '@/lib/notificationStore';

interface UserData {
  id: number;
  email: string;
  username: string;
  is_active: boolean;
  is_superuser: boolean;
}

export default function AdminSettings() {
  const [users, setUsers] = useState<UserData[]>([]);
  const [loading, setLoading] = useState(true);
  const { addNotification } = useNotificationStore();
  
  // Create User State
  const [isCreating, setIsCreating] = useState(false);
  const [newUser, setNewUser] = useState({ email: '', username: '', password: '', is_superuser: false });

  // Edit User State
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<Partial<UserData>>({});

  const fetchUsers = async () => {
    setLoading(true);
    try {
      const data = await getUsers();
      setUsers(data);
    } catch (err: any) {
      addNotification({ message: 'Failed to fetch users', type: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createUser(newUser);
      addNotification({ message: 'User created successfully', type: 'success' });
      setIsCreating(false);
      setNewUser({ email: '', username: '', password: '', is_superuser: false });
      fetchUsers();
    } catch (err: any) {
      addNotification({ message: err.response?.data?.detail || 'Failed to create user', type: 'error' });
    }
  };

  const handleDeleteUser = async (id: number) => {
    if (!confirm('Are you sure? This will delete all data associated with this user.')) return;
    try {
      await deleteUser(id);
      addNotification({ message: 'User deleted successfully', type: 'success' });
      setUsers(users.filter(u => u.id !== id));
    } catch (err: any) {
      addNotification({ message: 'Failed to delete user', type: 'error' });
    }
  };

  const startEdit = (user: UserData) => {
    setEditingId(user.id);
    setEditForm({ email: user.email, username: user.username, is_superuser: user.is_superuser, is_active: user.is_active });
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditForm({});
  };

  const saveEdit = async (id: number) => {
    try {
      await updateUser(id, editForm);
      addNotification({ message: 'User updated successfully', type: 'success' });
      setEditingId(null);
      fetchUsers();
    } catch (err: any) {
      addNotification({ message: 'Failed to update user', type: 'error' });
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h3 className="text-lg font-medium text-white flex items-center gap-2">
          <Shield className="w-5 h-5 text-purple-400" />
          User Management
        </h3>
        <button
          onClick={() => setIsCreating(!isCreating)}
          className="bg-purple-600 hover:bg-purple-700 text-white px-3 py-1.5 rounded text-sm flex items-center gap-2"
        >
          <UserPlus className="w-4 h-4" />
          Add User
        </button>
      </div>

      {isCreating && (
        <div className="bg-gray-800/50 p-4 rounded border border-purple-500/30 mb-4">
          <h4 className="text-sm font-medium text-purple-300 mb-3">New User Details</h4>
          <form onSubmit={handleCreateUser} className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <input
              placeholder="Username"
              value={newUser.username}
              onChange={e => setNewUser({...newUser, username: e.target.value})}
              className="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white"
              required
            />
            <input
              placeholder="Email"
              type="email"
              value={newUser.email}
              onChange={e => setNewUser({...newUser, email: e.target.value})}
              className="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white"
              required
            />
            <input
              placeholder="Password"
              type="password"
              value={newUser.password}
              onChange={e => setNewUser({...newUser, password: e.target.value})}
              className="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white"
              required
            />
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="is_superuser"
                checked={newUser.is_superuser}
                onChange={e => setNewUser({...newUser, is_superuser: e.target.checked})}
                className="rounded bg-gray-900 border-gray-700"
              />
              <label htmlFor="is_superuser" className="text-sm text-gray-300">Is Admin?</label>
            </div>
            <div className="md:col-span-2 flex justify-end gap-2">
              <button type="button" onClick={() => setIsCreating(false)} className="text-gray-400 hover:text-white text-sm px-3 py-1">Cancel</button>
              <button type="submit" className="bg-purple-600 hover:bg-purple-700 text-white px-3 py-1 rounded text-sm">Create User</button>
            </div>
          </form>
        </div>
      )}

      <div className="bg-gray-800/50 rounded-lg border border-gray-700 overflow-hidden">
        <table className="w-full text-left text-sm text-gray-400">
          <thead className="bg-gray-900/50 text-gray-200 uppercase font-medium">
            <tr>
              <th className="px-4 py-3">ID</th>
              <th className="px-4 py-3">Username</th>
              <th className="px-4 py-3">Email</th>
              <th className="px-4 py-3">Role</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-700">
            {loading ? (
              <tr><td colSpan={6} className="p-4 text-center"><Loader2 className="w-5 h-5 animate-spin mx-auto" /></td></tr>
            ) : users.map(user => (
              <tr key={user.id} className="hover:bg-gray-700/30">
                <td className="px-4 py-3">{user.id}</td>
                
                {/* Username */}
                <td className="px-4 py-3">
                  {editingId === user.id ? (
                    <input 
                      value={editForm.username} 
                      onChange={e => setEditForm({...editForm, username: e.target.value})}
                      className="bg-gray-900 border border-gray-600 rounded px-2 py-1 w-full"
                    />
                  ) : user.username}
                </td>

                {/* Email */}
                <td className="px-4 py-3">
                  {editingId === user.id ? (
                    <input 
                      value={editForm.email} 
                      onChange={e => setEditForm({...editForm, email: e.target.value})}
                      className="bg-gray-900 border border-gray-600 rounded px-2 py-1 w-full"
                    />
                  ) : user.email}
                </td>

                {/* Role */}
                <td className="px-4 py-3">
                  {editingId === user.id ? (
                    <label className="flex items-center gap-2">
                      <input 
                        type="checkbox" 
                        checked={editForm.is_superuser} 
                        onChange={e => setEditForm({...editForm, is_superuser: e.target.checked})}
                      /> Admin
                    </label>
                  ) : (
                    <span className={`px-2 py-0.5 rounded text-xs ${user.is_superuser ? 'bg-purple-900/50 text-purple-300' : 'bg-gray-700 text-gray-300'}`}>
                      {user.is_superuser ? 'Admin' : 'User'}
                    </span>
                  )}
                </td>

                {/* Status */}
                <td className="px-4 py-3">
                   {editingId === user.id ? (
                    <label className="flex items-center gap-2">
                      <input 
                        type="checkbox" 
                        checked={editForm.is_active} 
                        onChange={e => setEditForm({...editForm, is_active: e.target.checked})}
                      /> Active
                    </label>
                  ) : (
                    <span className={`px-2 py-0.5 rounded text-xs ${user.is_active ? 'bg-green-900/50 text-green-300' : 'bg-red-900/50 text-red-300'}`}>
                      {user.is_active ? 'Active' : 'Inactive'}
                    </span>
                  )}
                </td>

                {/* Actions */}
                <td className="px-4 py-3 text-right flex justify-end gap-2">
                  {editingId === user.id ? (
                    <>
                      <button onClick={() => saveEdit(user.id)} className="text-green-400 hover:text-green-300"><Check className="w-4 h-4" /></button>
                      <button onClick={cancelEdit} className="text-gray-400 hover:text-gray-300"><X className="w-4 h-4" /></button>
                    </>
                  ) : (
                    <>
                      <button onClick={() => startEdit(user)} className="text-blue-400 hover:text-blue-300"><Edit2 className="w-4 h-4" /></button>
                      <button onClick={() => handleDeleteUser(user.id)} className="text-red-400 hover:text-red-300"><Trash2 className="w-4 h-4" /></button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
