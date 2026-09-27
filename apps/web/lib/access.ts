/** Display policy only; every operation is authorized again by the API. */
export interface Access {
  plan: string;
  free_launch_mode: boolean;
  billing_enabled: boolean;
  effective_access: 'plan' | 'full_access';
  entitlements: Record<string, boolean | number>;
}

export function showPlanUi(access: Access | null): boolean {
  return access !== null && access.free_launch_mode === false;
}

export function showPlanLock(access: Access | null, reason: string | null): boolean {
  return reason !== null && (reason === 'PERMISSION' || showPlanUi(access));
}

export const launchCopy = {
  en: {
    currentAccess: 'Current access',
    fullAccess: 'Full access — Free launch',
    message: 'All ecomsbd features are currently available for free.',
    plans: 'Plans',
    unavailable: 'Plan purchases are currently unavailable.',
  },
  bn: {
    currentAccess: 'বর্তমান অ্যাক্সেস',
    fullAccess: 'সম্পূর্ণ অ্যাক্সেস — ফ্রি লঞ্চ',
    message: 'ecomsbd-এর সব ফিচার বর্তমানে বিনামূল্যে ব্যবহার করা যাচ্ছে।',
    plans: 'প্ল্যান',
    unavailable: 'প্ল্যান কেনা বর্তমানে বন্ধ আছে।',
  },
};
