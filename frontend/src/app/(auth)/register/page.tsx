'use client';

import { useState, useEffect, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import Image from 'next/image';
import { registerUser, validateInvitation, login } from '@/lib/api';
import { getErrorMessage } from '@/lib/errors';
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
  const [isValidating, setIsValidating] = useState(true);

  useEffect(() => {
    const checkInvite = async () => {
      if (!inviteCode) {
        setIsValidating(false);
        return;
      }

      try {
        await validateInvitation(inviteCode);
        setFormData(prev => ({ ...prev, invite_code: inviteCode }));

            } catch (e: unknown) {
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
      await login(formData.username, formData.password);

      // Redirect to dashboard (or setup if needed, but usually dashboard)
      router.push('/');

        } catch (err: unknown) {
      setError(getErrorMessage(err, "Registration failed. Please check your details and invite code."));
    } finally {
      setLoading(false);
    }
  };

  if (isValidating) {
    return (
      <div className="min-h-dvh flex items-center justify-center bg-surface-page">
        <Loader2 className="w-8 h-8 animate-spin text-action-text" />
      </div>
    );
  }

  return (
    <div className="min-h-dvh flex items-center justify-center bg-surface-page px-4 sm:px-6 lg:px-8">
      <div className="max-w-md w-full space-y-8 p-10 bg-surface-card rounded-surface border border-surface-border shadow-card">
        <div className="flex flex-col items-center justify-center">
          <div className="flex flex-col items-center gap-4 mb-2">
            <Image
              src="/assets/NojoinLogo.png"
              alt="Nojoin Logo"
              width={68}
              height={68}
              className="object-contain"
            />
            <h2 className="text-3xl font-bold text-action-text">
              Nojoin
            </h2>
          </div>
          <h2 className="mt-4 text-center text-2xl font-bold text-foreground">
            Create your account
          </h2>
          <p className="mt-2 text-center text-sm text-contrast-helper">
            Or{' '}
            <Link href="/login" className="font-medium text-action-text hover:text-action-text-hover">
              sign in to your existing account
            </Link>
          </p>
        </div>

        <form
          id="register-form"
          name="register-form"
          method="post"
          className="mt-8 space-y-6"
          onSubmit={handleSubmit}
          autoComplete="on"
        >
          <div className="space-y-4">
            <div className="relative">
              <label htmlFor="register-username" className="sr-only">
                Username
              </label>
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <User className="h-5 w-5 text-contrast-icon-muted" />
              </div>
              <input
                id="register-username"
                name="register-username"
                type="text"
                autoComplete="section-register username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                aria-describedby={error ? 'register-error' : undefined}
                aria-invalid={Boolean(error)}
                required
                className="appearance-none block w-full pl-10 pr-3 py-3 border border-control-border rounded-lg bg-control-bg text-foreground placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring sm:text-sm transition-colors"
                placeholder="Username"
                value={formData.username}
                onChange={(e) => setFormData({ ...formData, username: e.target.value })}
              />
            </div>

            <div className="relative">
              <label htmlFor="register-new-password" className="sr-only">
                Password
              </label>
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <Lock className="h-5 w-5 text-contrast-icon-muted" />
              </div>
              <input
                id="register-new-password"
                name="register-new-password"
                type="password"
                autoComplete="section-register new-password"
                aria-describedby={error ? 'register-error' : undefined}
                aria-invalid={Boolean(error)}
                required
                minLength={8}
                className="appearance-none block w-full pl-10 pr-3 py-3 border border-control-border rounded-lg bg-control-bg text-foreground placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring sm:text-sm transition-colors"
                placeholder="Password"
                value={formData.password}
                onChange={(e) => setFormData({ ...formData, password: e.target.value })}
              />
            </div>

            <div className="relative">
              <label htmlFor="register-confirm-password" className="sr-only">
                Confirm password
              </label>
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <Lock className="h-5 w-5 text-contrast-icon-muted" />
              </div>
              <input
                id="register-confirm-password"
                name="register-confirm-password"
                type="password"
                autoComplete="section-register new-password"
                aria-describedby={error ? 'register-error' : undefined}
                aria-invalid={Boolean(error)}
                required
                minLength={8}
                className="appearance-none block w-full pl-10 pr-3 py-3 border border-control-border rounded-lg bg-control-bg text-foreground placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring sm:text-sm transition-colors"
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
                <label htmlFor="invite-code" className="block text-sm font-medium text-contrast-muted mb-1">
                  Invite Code
                </label>
                <input
                  id="invite-code"
                  name="register-invite-code"
                  type="text"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  required
                  className="appearance-none block w-full px-3 py-3 border border-control-border rounded-lg bg-control-bg text-foreground placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring sm:text-sm transition-colors"
                  placeholder="Enter your invite code"
                  value={formData.invite_code}
                  onChange={(e) => setFormData({ ...formData, invite_code: e.target.value })}
                />
            </div>
          )}

          {error && (
            <div
              id="register-error"
              role="alert"
              aria-live="polite"
              className="text-status-danger-fg text-sm text-center bg-status-danger-bg p-3 rounded-lg border border-status-danger-border"
            >
              {error}
            </div>
          )}

          <div>
            <button
              type="submit"
              disabled={loading}
              className="group relative w-full flex justify-center py-3 px-4 border border-transparent text-sm font-medium rounded-lg text-action-on bg-action hover:bg-action-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
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
    <Suspense fallback={<div className="min-h-dvh flex items-center justify-center"><Loader2 className="w-8 h-8 animate-spin text-action-text" /></div>}>
      <RegisterForm />
    </Suspense>
  );
}
