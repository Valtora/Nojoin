import { createContext, useContext } from "react";
import { Monitor, Users } from "lucide-react";

import SettingsStatusBadge from "./SettingsStatusBadge";
import type { SettingsScope } from "./settingsCategories";
import { getSettingsScope } from "./settingsRegistry";

const SCOPES: Record<
  SettingsScope,
  { label: string; title: string; Icon: typeof Users }
> = {
  everyone: {
    label: "Everyone",
    title: "Applies to the whole installation",
    Icon: Users,
  },
  browser: {
    label: "This browser",
    title: "Saved in this browser only, not with your account",
    Icon: Monitor,
  },
};

/**
 * Who a setting applies to, when that is not just you. Settings are per
 * account unless marked, so only install-wide and browser-only settings carry
 * one. The scope comes from the registry entry the card, row or block renders.
 */
export default function SettingsScopeBadge({ scope }: { scope: SettingsScope }) {
  const { label, title, Icon } = SCOPES[scope];

  return (
    <span title={title}>
      <SettingsStatusBadge tone="neutral" className="gap-1 px-2 py-0.5 font-medium">
        <Icon className="h-3 w-3" aria-hidden="true" />
        {label}
        <span className="sr-only">: {title.toLowerCase()}</span>
      </SettingsStatusBadge>
    </span>
  );
}

/**
 * The same scope as a line of small text, for places too narrow for a pill,
 * such as a search result under the navigation.
 */
export function SettingsScopeText({ scope }: { scope: SettingsScope }) {
  const { label, title, Icon } = SCOPES[scope];

  return (
    <span title={title} className="inline-flex shrink-0 items-center gap-1">
      <Icon className="h-3 w-3" aria-hidden="true" />
      {label}
    </span>
  );
}

/** The scope a card shows on its title, so the rows inside do not repeat it. */
export const SettingsCardScope = createContext<SettingsScope | undefined>(undefined);

/**
 * The badge for a row or block with this registry id, or nothing when it has
 * no scope or its card already shows the same one.
 */
export function useScopeBadge(id: string | undefined) {
  const cardScope = useContext(SettingsCardScope);
  const scope = getSettingsScope(id);

  return scope && scope !== cardScope ? <SettingsScopeBadge scope={scope} /> : null;
}
