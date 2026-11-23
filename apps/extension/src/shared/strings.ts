/** Side panel copy, EN + BN (every key in both; test/manifest.test.mjs). */

export type Locale = 'en' | 'bn';

export const en = {
  title: 'ecomsbd order capture',
  pairTitle: 'Link this browser to your shop',
  pairHint: 'In ecomsbd on the web, open Settings → Browser extension and get a 6-digit code.',
  codeLabel: '6-digit code',
  pair: 'Link',
  pairing: 'Linking…',
  invalidCode: 'That code is not valid or has expired. Get a new one.',
  linked: 'Linked to your shop (key ••••{last4})',
  capture: 'Capture',
  capturing: 'Capturing…',
  unsupported: "Ei page ekhon support na; text select kore 'Capture' korun",
  selectText: "Select the customer's message on the page, then tap Capture.",
  tooLong: 'That selection is too long. Select only the order message.',
  notAChat: 'Open a chat in Messenger, Meta Business Suite or WhatsApp Web.',
  fields: 'What was found',
  phone: 'Phone',
  address: 'Address',
  items: 'Items',
  nameCod: 'Name and COD',
  found: 'Found',
  missing: 'Missing',
  checkInApp: 'Check in ecomsbd',
  open: 'Open in ecomsbd',
  again: 'Try again',
  captured: 'Captured {count} new message(s).',
  nothingNew: 'Nothing new since the last capture.',
  offline: 'No connection. Your capture is kept; try again.',
  rateLimited: 'Too many captures. Wait a minute and try again.',
  disabled: 'The browser extension is not enabled for your shop.',
  unlinked: 'This browser is no longer linked. Link it again with a new code.',
  failed: 'Something went wrong. Try again.',
  unpair: 'Unlink this browser',
  privacy: 'Only the chat you have open is read, and only when you tap Capture.',
  language: 'বাংলা',
} as const;

export type StringKey = keyof typeof en;

export const bn: Record<StringKey, string> = {
  title: 'ecomsbd অর্ডার ক্যাপচার',
  pairTitle: 'এই ব্রাউজার আপনার শপের সাথে যুক্ত করুন',
  pairHint: 'ওয়েবে ecomsbd-এ Settings → Browser extension খুলে ৬ সংখ্যার কোড নিন।',
  codeLabel: '৬ সংখ্যার কোড',
  pair: 'যুক্ত করুন',
  pairing: 'যুক্ত হচ্ছে…',
  invalidCode: 'কোডটি সঠিক নয় বা মেয়াদ শেষ। নতুন কোড নিন।',
  linked: 'আপনার শপের সাথে যুক্ত (কী ••••{last4})',
  capture: 'Capture',
  capturing: 'ক্যাপচার হচ্ছে…',
  unsupported: "Ei page ekhon support na; text select kore 'Capture' korun",
  selectText: 'পেজে কাস্টমারের মেসেজ সিলেক্ট করে Capture চাপুন।',
  tooLong: 'সিলেকশন অনেক বড়। শুধু অর্ডারের মেসেজটি সিলেক্ট করুন।',
  notAChat: 'Messenger, Meta Business Suite বা WhatsApp Web-এ একটি চ্যাট খুলুন।',
  fields: 'যা পাওয়া গেছে',
  phone: 'ফোন',
  address: 'ঠিকানা',
  items: 'পণ্য',
  nameCod: 'নাম ও COD',
  found: 'পাওয়া গেছে',
  missing: 'নেই',
  checkInApp: 'ecomsbd-এ দেখুন',
  open: 'ecomsbd-এ খুলুন',
  again: 'আবার চেষ্টা করুন',
  captured: '{count}টি নতুন মেসেজ ক্যাপচার হয়েছে।',
  nothingNew: 'আগের ক্যাপচারের পর নতুন কিছু নেই।',
  offline: 'ইন্টারনেট নেই। ক্যাপচার রাখা আছে; আবার চেষ্টা করুন।',
  rateLimited: 'অনেক বেশি ক্যাপচার। এক মিনিট পর আবার চেষ্টা করুন।',
  disabled: 'আপনার শপের জন্য ব্রাউজার এক্সটেনশন চালু নেই।',
  unlinked: 'এই ব্রাউজার আর যুক্ত নেই। নতুন কোড দিয়ে আবার যুক্ত করুন।',
  failed: 'কিছু একটা ভুল হয়েছে। আবার চেষ্টা করুন।',
  unpair: 'এই ব্রাউজার আনলিংক করুন',
  privacy: 'শুধু খোলা চ্যাটটি পড়া হয়, আর শুধু Capture চাপলেই।',
  language: 'English',
};

export function translate(locale: Locale, key: StringKey, vars: Record<string, string | number> = {}): string {
  const template = (locale === 'bn' ? bn : en)[key];
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => (name in vars ? String(vars[name]) : whole));
}
