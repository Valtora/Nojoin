'use client';

import { useState, useEffect, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import Image from 'next/image';
import { registerUser, validateInvitation, login } from '@/lib/api';
import { Loader2, User, Lock } from 'lucide-react';

function RegisterForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  // Support both 'invite' (from backend link) and 'invite_code' (legacy/manual)
  const inviteCode = searchParams.get('invite') || searchParams.get('invite_code');

  const [formData, setFormData] = useState({
    username: '',
    password: '',
    confirmPassword: '',
    invite_code: inviteCode || '',
  });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [inviterName, setInviterName] = useState<string | null>(null);
  const [isValidating, setIsValidating] = useState(true);

  useEffect(() => {
    const checkInvite = async () => {
      if (!inviteCode) {
        setIsValidating(false);
        return;
      }
      
      try {
        const res = await validateInvitation(inviteCode);
        if (res.valid && res.inviter) {
          setInviterName(res.inviter);
        }
        setFormData(prev => ({ ...prev, invite_code: inviteCode }));
      } catch (e) {
        console.error("Invalid invite code", e);
        setError("Invalid or expired invite code.");
      } finally {
        setIsValidating(false);
      }
    };
    
    checkInvite();
  }, [inviteCode]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    if (formData.password !== formData.confirmPassword) {
      setError("Passwords do not match");
      setLoading(false);
      return;
    }

    try {
      await registerUser(
        formData.username,
        formData.password,
        formData.invite_code
      );
      
      // Auto-login after registration
      const loginRes = await login(formData.username, formData.password);
      localStorage.setItem('token', loginRes.access_token);
      
      // Redirect to dashboard (or setup if needed, but usually dashboard)
      router.push('/');
    } catch (err: any) {
      setError(err.response?.data?.detail || "Registration failed. Please check your details and invite code.");
    } finally {
      setLoading(false);
    }
  };

  if (isValidating) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950">
        <Loader2 className="w-8 h-8 animate-spin text-orange-600" />
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950 px-4 sm:px-6 lg:px-8">
      <div className="max-w-md w-full space-y-8 p-10 bg-white dark:bg-gray-900 rounded-2xl shadow-xl border border-gray-100 dark:border-gray-800">
        <div className="flex flex-col items-center justify-center">
          <div className="flex flex-col items-center gap-4 mb-2">
            <Image 
              src="/assets/NojoinLogo.png" 
              alt="Nojoin Logo" 
              width={68} 
              height={68} 
              className="object-contain"
            />
            <h2 className="text-3xl font-bold text-orange-600">
              Nojoin
            </h2>
          </div>
          <h2 className="mt-4 text-center text-2xl font-bold text-gray-900 dark:text-white">
            Create your account
          </h2>
          {inviterName && (
            <p className="mt-2 text-center text-sm text-gray-600 dark:text-gray-400">
              Welcome, <span className="font-medium text-orange-600">{inviterName}</span> has invited you to join their Nojoin instance.
            </p>
          )}
          <p className="mt-2 text-center text-sm text-gray-600 dark:text-gray-400">
            Or{' '}
            <Link href="/login" className="font-medium text-orange-600 hover:text-orange-500">
              sign in to your existing account
            </Link>
          </p>
        </div>

        <form className="mt-8 space-y-6" onSubmit={handleSubmit}>
          <div className="space-y-4">
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <User className="h-5 w-5 text-gray-400" />
              </div>
              <input
                id="username"
                name="username"
                type="text"
                required
                className="appearance-none block w-full pl-10 pr-3 py-3 border border-gray-300 dark:border-gray-700 rounded-lg placeholder-gray-400 text-gray-900 dark:text-white dark:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent sm:text-sm transition-colors"
                placeholder="Username"
                value={formData.username}
                onChange={(e) => setFormData({ ...formData, username: e.target.value })}
              />
            </div>
            
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <Lock className="h-5 w-5 text-gray-400" />
              </div>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="new-password"
                required
                className="appearance-none block w-full pl-10 pr-3 py-3 border border-gray-300 dark:border-gray-700 rounded-lg placeholder-gray-400 text-gray-900 dark:text-white dark:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent sm:text-sm transition-colors"
                placeholder="Password"
                value={formData.password}
                onChange={(e) => setFormData({ ...formData, password: e.target.value })}
              />
            </div>

            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <Lock className="h-5 w-5 text-gray-400" />
              </div>
              <input
                id="confirm-password"
                name="confirmPassword"
                type="password"
                autoComplete="new-password"
                required
                className="appearance-none block w-full pl-10 pr-3 py-3 border border-gray-300 dark:border-gray-700 rounded-lg placeholder-gray-400 text-gray-900 dark:text-white dark:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent sm:text-sm transition-colors"
                placeholder="Confirm Password"
                value={formData.confirmPassword}
                onChange={(e) => setFormData({ ...formData, confirmPassword: e.target.value })}
              />
            </div>
          </div>

          {/* Hidden invite code field, or visible if missing/error? 
              User requested it not be necessary. We'll keep it in state but not show input unless there's no code in URL?
              Actually, if there is no code in URL, they probably shouldn't be here or should enter it manually.
              Let's show it only if it wasn't in the URL.
          */}
          {!inviteCode && (
             <div>
                <label htmlFor="invite-code" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                  Invite Code
                </label>
                <input
                  id="invite-code"
                  name="inviteCode"
                  type="text"
                  required
                  className="appearance-none block w-full px-3 py-3 border border-gray-300 dark:border-gray-700 rounded-lg placeholder-gray-400 text-gray-900 dark:text-white dark:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent sm:text-sm transition-colors"
                  placeholder="Enter your invite code"
                  value={formData.invite_code}
                  onChange={(e) => setFormData({ ...formData, invite_code: e.target.value })}
                />
            </div>
          )}

          {error && (
            <div className="text-red-500 text-sm text-center bg-red-50 dark:bg-red-900/20 p-2 rounded">
              {error}
            </div>
          )}

          <div>
            <button
              type="submit"
              disabled={loading}
              className="group relative w-full flex justify-center py-3 px-4 border border-transparent text-sm font-medium rounded-lg text-white bg-orange-600 hover:bg-orange-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-orange-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-lg shadow-orange-600/20"
            >
              {loading ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : (
                "Register"
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function RegisterPage() {
  return (
    <Suspense fallback={<div className="min-h-screen flex items-center justify-center"><Loader2 className="w-8 h-8 animate-spin text-orange-500" /></div>}>
      <RegisterForm />
    </Suspense>
  );
}
