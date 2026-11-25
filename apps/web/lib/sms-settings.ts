/**
 * Settings → SMS (launch run 14): the shop's own SMS switches, both off until
 * the seller turns them on, because every SMS costs from the SMS balance.
 *
 * - SMS confirm link: `GET/PATCH /v1/shop/sms-settings` (`confirmation_link`).
 * - Order update SMS: the messaging channel `SMS` (`POST /v1/messaging/channels/SMS`).
 *
 * Same meaning as the phone's `ss.*` strings.
 */

export type SmsSwitch = { available: boolean; enabled: boolean };
export type SmsSettings = { confirmation_link: SmsSwitch };
export type ChannelRow = { kind: string; available: boolean; enabled: boolean; shop_blocker?: string | null };

/** The order-update SMS switch, from `GET /v1/messaging/channels`. */
export function orderUpdateSwitch(rows: readonly ChannelRow[] | null | undefined): SmsSwitch {
  const row = rows?.find((r) => r.kind === 'SMS');
  return { available: Boolean(row?.available && !row.shop_blocker), enabled: Boolean(row?.enabled) };
}

/** A switch can be changed when it is available, and can always be turned off. */
export function canToggle(state: SmsSwitch): boolean {
  return state.available || state.enabled;
}

export const smsSettingsEn = {
  'nav.sms': 'SMS',
  'ss.subtitle': 'Text messages your shop sends to customers. Each one is off until you turn it on.',
  'ss.cost': 'Costs from your SMS balance.',
  'ss.linkTitle': 'SMS confirm link',
  'ss.linkHint':
    'When a customer does not answer your confirmation call, they get one SMS with a link to confirm or cancel the order. Never during your quiet hours.',
  'ss.linkSwitch': 'Send the confirm link by SMS',
  'ss.updatesTitle': 'Order update SMS',
  'ss.updatesHint': 'Order updates you set up in Customer messaging and Automation go out by SMS.',
  'ss.updatesSwitch': 'Send order updates by SMS',
  'ss.unavailable': 'Not available for your shop yet.',
  'ss.readOnly': 'Only a member who can manage settings can change these.',
  'ss.saved': 'Saved.',
} as const;

export type SmsSettingsKey = keyof typeof smsSettingsEn;

export const smsSettingsBn: Record<SmsSettingsKey, string> = {
  'nav.sms': 'SMS',
  'ss.subtitle': 'আপনার শপ থেকে গ্রাহককে যে SMS যায়। আপনি চালু না করা পর্যন্ত প্রতিটি বন্ধ থাকে।',
  'ss.cost': 'আপনার SMS ব্যালেন্স থেকে খরচ হয়।',
  'ss.linkTitle': 'SMS-এ কনফার্ম লিংক',
  'ss.linkHint':
    'গ্রাহক আপনার কনফার্মেশন কল না ধরলে, অর্ডার কনফার্ম বা বাতিল করার লিংকসহ একটি SMS পান। কোয়াইট আওয়ারে কখনো নয়।',
  'ss.linkSwitch': 'কনফার্ম লিংক SMS-এ পাঠান',
  'ss.updatesTitle': 'অর্ডার আপডেট SMS',
  'ss.updatesHint': 'গ্রাহককে মেসেজ ও অটোমেশনে আপনি যে অর্ডার আপডেট সেট করেছেন, সেগুলো SMS-এ যায়।',
  'ss.updatesSwitch': 'অর্ডারের আপডেট SMS-এ পাঠান',
  'ss.unavailable': 'আপনার শপে এখনো চালু হয়নি।',
  'ss.readOnly': 'শুধু সেটিংস পরিচালনার অনুমতি থাকা সদস্য এগুলো বদলাতে পারেন।',
  'ss.saved': 'সেভ হয়েছে।',
};
