import { KeyRound, Loader2 } from 'lucide-react';
import { useState } from 'react';

import { ButtonBase } from '@/components/ui/nativeBases';
import { loadingButtonProps } from '@/components/ui/loadingButton';
import { ssoErrorMessage, startSsoLink } from '@/lib/api';
import { getErrorMessage } from '@/lib/errors';
import { useLocationSearchParam } from '@/lib/useLocationSearchParam';
import type { User } from '@/types';
import SettingsBlock from './SettingsBlock';
import SettingsCallout from './SettingsCallout';
import SettingsCard from './SettingsCard';
import SettingsRow from './SettingsRow';
import { SETTINGS_BUTTON_PRIMARY } from './settingsControls';

interface SingleSignOnSettingsProps {
  user: User;
  providerName: string;
}

/**
 * Shows how the account relates to the identity provider, and lets a local
 * account link itself so it can sign in through the provider from then on.
 *
 * The callback returns here with `?sso_linked=1` or `?sso_error=<code>`.
 */
export default function SingleSignOnSettings({ user, providerName }: SingleSignOnSettingsProps) {
  const [linking, setLinking] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);
  const linkedParam = useLocationSearchParam('sso_linked');
  const errorParam = useLocationSearchParam('sso_error');

  const message: { tone: 'success' | 'error'; text: string } | null = linkError
    ? { tone: 'error', text: linkError }
    : errorParam
      ? { tone: 'error', text: ssoErrorMessage(errorParam) }
      : linkedParam === '1'
        ? { tone: 'success', text: `This account is now linked to ${providerName}.` }
        : null;

  const handleLink = async () => {
    setLinking(true);
    setLinkError(null);
    try {
      const url = await startSsoLink();
      window.location.assign(url);
    } catch (error: unknown) {
      setLinkError(getErrorMessage(error, 'Could not start linking.'));
      setLinking(false);
    }
  };

  const groups = user.oidc_groups ?? [];

  return (
    <SettingsCard
      id="profile-sso"
      title="Single sign-on"
      description={`Sign in to Nojoin with your ${providerName} account.`}
    >
      {message ? (
        <SettingsBlock>
          <SettingsCallout tone={message.tone} message={message.text} />
        </SettingsBlock>
      ) : null}

      {user.oidc_linked ? (
        <>
          <SettingsRow label="Linked identity" controlId="account-sso-identity">
            <p id="account-sso-identity" className="text-sm text-foreground">
              {user.display_name || user.email || user.username}
              {user.email && user.display_name ? (
                <span className="text-contrast-muted"> ({user.email})</span>
              ) : null}
            </p>
          </SettingsRow>
          <SettingsRow label="Groups" controlId="account-sso-groups">
            <p id="account-sso-groups" className="text-sm text-foreground">
              {groups.length > 0 ? groups.join(', ') : 'None'}
            </p>
          </SettingsRow>
          {user.auth_source === 'oidc' ? (
            <SettingsBlock>
              <SettingsCallout
                tone="info"
                message={`This account was created by ${providerName} and has no password. Groups update each time you sign in.`}
              />
            </SettingsBlock>
          ) : null}
        </>
      ) : (
        <SettingsBlock className="flex items-center justify-between gap-4">
          <p className="text-sm text-contrast-helper">
            Link this account to {providerName} to sign in without your Nojoin password.
            You will be asked to sign in at {providerName} again.
          </p>
          <ButtonBase
            data-ui-fill="action"
            data-ui-hover="action-hover"
            data-ui-active="action-active"
            data-ui-border="full"
            chrome="action"
            type="button"
            {...loadingButtonProps(linking, undefined, () => void handleLink())}
            className={SETTINGS_BUTTON_PRIMARY}
          >
            {linking ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <KeyRound className="h-4 w-4" aria-hidden="true" />
            )}
            Link {providerName}
          </ButtonBase>
        </SettingsBlock>
      )}
    </SettingsCard>
  );
}
