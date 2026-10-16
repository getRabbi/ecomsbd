/** `GET /v1/me`: the signed-in member, their shops, and the active one. */

export interface ShopSummary {
  id: string;
  name: string;
  role: string;
  onboarding_complete: boolean;
}

export interface Me {
  user_id: string;
  display_name: string | null;
  masked_phone: string | null;
  locale: string;
  tenant_id: string | null;
  role: string | null;
  permissions: string[];
  tenants: ShopSummary[];
  needs_onboarding: boolean;
}

/**
 * Whether the workspace can open: a shop is selected and its setup is done.
 *
 * `needs_onboarding` alone is not enough — the API also sets it for a member of
 * several shops whose session has none selected yet, who needs a picker, not a
 * setup form. And a member who cannot edit the shop works in it as it is
 * rather than being stopped at a form they could not save.
 */
export function workspaceReady(me: Me): boolean {
  if (me.tenant_id === null) {
    return false;
  }
  const current = me.tenants.find((shop) => shop.id === me.tenant_id);
  return !current || current.onboarding_complete || !me.permissions.includes('settings.manage');
}
