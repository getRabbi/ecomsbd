/// Copy for the confirmation call queue (V4 WP2.3).
///
/// `cq.reason.*` are the policy's reason codes (backend
/// `app/confirmations/policy.py` `ReasonCode`): why this order needs a call or
/// a look. The outcome buttons keep the seller's own words ("Dhoreni" = did
/// not pick up).
library;

const callQueueEn = <String, String>{
  'cq.title': 'Call queue',
  'cq.subtitle': 'Orders waiting for a confirmation call',
  'cq.banner': '{count} orders waiting for a call',
  'cq.notEnabled': 'Customer confirmation is not turned on for your shop yet.',
  'cq.loadFailed': 'Could not load the call queue.',
  'cq.empty': 'No calls waiting. New orders that need a call appear here.',
  'cq.quietNow': 'Quiet hours now ({start}–{end}). Calls wait until {end}.',
  'cq.call': 'Call',
  'cq.calls': 'Calls: {count}',
  'cq.lastNoAnswer': 'Last call: no answer',
  'cq.lastStarted': 'Called, answer not saved yet',
  'cq.outcomeTitle': 'What did the customer say?',
  'cq.confirm': 'Confirm',
  'cq.cancel': 'Cancel',
  'cq.noAnswer': 'Dhoreni',
  'cq.later': 'Not now',
  'cq.confirmed': 'Order confirmed.',
  'cq.cancelled': 'Order cancelled.',
  'cq.noAnswerSaved': 'Saved: no answer. Try again later.',
  'cq.quietTitle': 'Quiet hours',
  'cq.quietBody':
      'Your shop does not call between {start} and {end}. Call anyway?',
  'cq.quietOverride': 'Call anyway',
  'cq.dialFailed': 'Could not open the phone app.',
  'cq.actionFailed': 'Could not save. Try again.',
  'cq.reason.CUSTOMER_BLOCKED': 'Blocked customer — check before sending',
  'cq.reason.HIGH_RISK': 'High return risk',
  'cq.reason.HIGH_VALUE': 'High COD — call to confirm',
  'cq.reason.TRUSTED_REPEAT': 'Trusted repeat customer',
  'cq.reason.TRUSTED_NETWORK': 'Trusted across shops',
  'cq.reason.VERIFY_NEW': 'New customer — call to confirm',
  'cq.reason.SHOP_MANUAL': 'You confirm every order yourself',
  'cq.reason.CALL_EVERYONE': 'Your shop calls every customer',
  'cq.reason.other': 'Needs a look',
};

const callQueueBn = <String, String>{
  'cq.title': 'কল তালিকা',
  'cq.subtitle': 'কনফার্মেশন কলের অপেক্ষায় থাকা অর্ডার',
  'cq.banner': '{count}টি অর্ডার কলের অপেক্ষায়',
  'cq.notEnabled': 'আপনার দোকানে কাস্টমার কনফার্মেশন এখনো চালু হয়নি।',
  'cq.loadFailed': 'কল তালিকা লোড করা যায়নি।',
  'cq.empty': 'কোনো কল বাকি নেই। যেসব অর্ডারে কল লাগবে সেগুলো এখানে আসবে।',
  'cq.quietNow': 'এখন কল না করার সময় ({start}–{end})। {end} এর পর কল করুন।',
  'cq.call': 'কল',
  'cq.calls': 'কল: {count}',
  'cq.lastNoAnswer': 'শেষ কল: ধরেনি',
  'cq.lastStarted': 'কল হয়েছে, উত্তর সেভ হয়নি',
  'cq.outcomeTitle': 'কাস্টমার কী বললেন?',
  'cq.confirm': 'কনফার্ম',
  'cq.cancel': 'বাতিল',
  'cq.noAnswer': 'ধরেনি',
  'cq.later': 'এখন না',
  'cq.confirmed': 'অর্ডার কনফার্ম হয়েছে।',
  'cq.cancelled': 'অর্ডার বাতিল হয়েছে।',
  'cq.noAnswerSaved': 'সেভ হয়েছে: ধরেনি। পরে আবার চেষ্টা করুন।',
  'cq.quietTitle': 'কল না করার সময়',
  'cq.quietBody':
      'আপনার দোকান {start} থেকে {end} পর্যন্ত কল করে না। তবুও কল করবেন?',
  'cq.quietOverride': 'তবুও কল করুন',
  'cq.dialFailed': 'ফোন অ্যাপ খোলা যায়নি।',
  'cq.actionFailed': 'সেভ করা যায়নি। আবার চেষ্টা করুন।',
  'cq.reason.CUSTOMER_BLOCKED': 'ব্লক করা কাস্টমার — পাঠানোর আগে দেখুন',
  'cq.reason.HIGH_RISK': 'রিটার্নের ঝুঁকি বেশি',
  'cq.reason.HIGH_VALUE': 'বেশি COD — কল করে কনফার্ম করুন',
  'cq.reason.TRUSTED_REPEAT': 'বিশ্বস্ত পুরনো কাস্টমার',
  'cq.reason.TRUSTED_NETWORK': 'অন্য দোকানেও বিশ্বস্ত',
  'cq.reason.VERIFY_NEW': 'নতুন কাস্টমার — কল করে কনফার্ম করুন',
  'cq.reason.SHOP_MANUAL': 'আপনি নিজে প্রতিটি অর্ডার কনফার্ম করেন',
  'cq.reason.CALL_EVERYONE': 'আপনার দোকান সব কাস্টমারকে কল করে',
  'cq.reason.other': 'একবার দেখে নিন',
};

/// The policy's reason codes, in its rule order.
const callQueueReasonCodes = <String>[
  'CUSTOMER_BLOCKED',
  'HIGH_RISK',
  'HIGH_VALUE',
  'TRUSTED_REPEAT',
  'TRUSTED_NETWORK',
  'VERIFY_NEW',
  'SHOP_MANUAL',
  'CALL_EVERYONE',
];
