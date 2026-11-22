/**
 * V4 WP7.1: tracking SMS. SMS order updates go through the platform's Alpha SMS
 * gateway. The server says `available` only while that gateway sends (its flag
 * and its admin-console account on); `enabled` is the shop's own SMS switch,
 * which starts off.
 */

export type MessagingChannel = 'EMAIL' | 'WHATSAPP' | 'SMS';

export type ChannelState = { kind: string; available: boolean; enabled: boolean };

/** `off`: not offered here · `available`: the shop may switch it on · `ready`: it sends. */
export type SmsState = 'off' | 'available' | 'ready';

export function smsState(rows: readonly ChannelState[] | null | undefined): SmsState {
  const row = rows?.find((r) => r.kind === 'SMS');
  if (!row || !row.available) return 'off';
  return row.enabled ? 'ready' : 'available';
}

/** WhatsApp and SMS reach the customer's phone number. */
export function usesPhone(channel: string): boolean {
  return channel === 'WHATSAPP' || channel === 'SMS';
}

/** Three SMS parts: 3 x 67 UCS-2 characters (Bangla), 3 x 153 GSM characters. */
export const SMS_MAX_PARTS = 3;

export const smsEn = {
  'sms.label': 'SMS',
  'sms.off': 'SMS — not set up',
  'sms.turnOn': 'SMS — turn it on in Customer messaging',
  'sms.number': 'Mobile number',
  'sms.templateHint': 'SMS carries order updates only. Keep each language within 3 SMS parts (Bangla: about 200 characters).',
} as const;

export type SmsKey = keyof typeof smsEn;

export const smsBn: Record<SmsKey, string> = {
  'sms.label': 'SMS',
  'sms.off': 'SMS — সেটআপ হয়নি',
  'sms.turnOn': 'SMS — গ্রাহককে মেসেজ থেকে চালু করুন',
  'sms.number': 'মোবাইল নম্বর',
  'sms.templateHint': 'SMS-এ শুধু অর্ডারের আপডেট যায়। প্রতিটি ভাষা ৩টি SMS অংশের মধ্যে রাখুন (বাংলায় প্রায় ২০০ অক্ষর)।',
};

export function smsText(locale: string, key: SmsKey): string {
  return locale === 'bn' ? smsBn[key] : smsEn[key];
}

/** The option label for a channel picker: plain when usable, else why not. */
export function smsOptionLabel(locale: string, state: SmsState, needsShopSwitch: boolean): string {
  if (state === 'off') return smsText(locale, 'sms.off');
  if (needsShopSwitch && state !== 'ready') return smsText(locale, 'sms.turnOn');
  return smsText(locale, 'sms.label');
}
