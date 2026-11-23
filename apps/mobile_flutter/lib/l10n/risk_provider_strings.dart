/// External risk provider and network context copy for the phone (V3.7).
/// The shop's own history, a provider's facts and anonymous network figures
/// are always named apart.
library;

const riskProviderEn = <String, String>{
  'xr.title': 'External provider facts',
  'xr.hint':
      'What a provider you connected reports. Kept apart from your shop’s own history.',
  'xr.gated':
      'No licensed risk provider is available yet. Your shop’s own Risk Check works as usual.',
  'xr.notConfigured':
      'No risk provider is turned on. An owner can set one up on the web.',
  'xr.none': 'Not checked with {provider} yet.',
  'xr.found': '{provider} has records for this number',
  'xr.notFound': '{provider} has no records for this number',
  'xr.fresh': 'Up to date',
  'xr.stale': 'Out of date',
  'xr.checked': 'Checked {when}',
  'xr.history': 'Courier history',
  'xr.komData': 'Too little data (fewer than 3 parcels)',
  'xr.allCouriers': 'All couriers',
  'xr.observed': 'Provider data as of {when}',
  'xr.unavailable': 'Last check failed: {reason}',
  'xr.check': 'Check with provider',
  'xr.refresh': 'Refresh',
  'xr.cached': 'The stored answer is still current.',
  'xr.skipped': 'Not checked: {reason}.',
  'xr.fact.DELIVERED_PARCELS': 'Delivered parcels',
  'xr.fact.RETURNED_PARCELS': 'Returned parcels',
  'xr.fact.CANCELLED_ORDERS': 'Cancelled orders',
  'xr.fact.TOTAL_PARCELS': 'All parcels',
  'xr.err.PROVIDER_TIMEOUT': 'the provider took too long',
  'xr.err.PROVIDER_UNAVAILABLE': 'the provider is unavailable',
  'xr.err.PROVIDER_RATE_LIMITED': 'too many requests to the provider',
  'xr.err.PROVIDER_AUTH_FAILED': 'the provider rejected the credentials',
  'xr.err.PROVIDER_REJECTED': 'the provider refused the request',
  'xr.err.PROVIDER_BAD_RESPONSE': 'the provider’s answer could not be read',
  'xr.err.NEW_ORDER_REQUIRED':
      'this provider checks only a customer with a new order (draft or confirmed, last 3 days)',
  'xr.net.title': 'Network context',
  'xr.net.hint':
      'Anonymous monthly figures from consenting shops. About the network, not this customer.',
  'xr.net.none': 'No anonymous figures published yet.',
  'xr.net.insufficient': 'Data not sufficient',
  'xr.net.RTO_RATE': 'Network return (RTO) rate',
  'xr.net.DELIVERY_SUCCESS': 'Network delivery success',
  'xr.net.period': 'Month {period} (UTC)',
};

const riskProviderBn = <String, String>{
  'xr.title': 'বাইরের প্রোভাইডারের তথ্য',
  'xr.hint':
      'আপনার যুক্ত করা প্রোভাইডার যা জানায়। আপনার শপের নিজস্ব ইতিহাস থেকে আলাদা রাখা হয়।',
  'xr.gated':
      'এখনো কোনো লাইসেন্সপ্রাপ্ত রিস্ক প্রোভাইডার নেই। আপনার শপের নিজস্ব রিস্ক চেক আগের মতোই চলছে।',
  'xr.notConfigured':
      'কোনো রিস্ক প্রোভাইডার চালু নেই। মালিক ওয়েবে সেট আপ করতে পারেন।',
  'xr.none': '{provider}-এ এখনো দেখা হয়নি।',
  'xr.found': '{provider}-এর কাছে এই নম্বরের রেকর্ড আছে',
  'xr.notFound': '{provider}-এর কাছে এই নম্বরের কোনো রেকর্ড নেই',
  'xr.fresh': 'হালনাগাদ',
  'xr.stale': 'পুরোনো',
  'xr.checked': 'দেখা হয়েছে {when}',
  'xr.history': 'কুরিয়ার ইতিহাস',
  'xr.komData': 'কম ডেটা (৩টির কম পার্সেল)',
  'xr.allCouriers': 'সব কুরিয়ার',
  'xr.observed': '{when} পর্যন্ত প্রোভাইডারের তথ্য',
  'xr.unavailable': 'শেষ চেষ্টা ব্যর্থ: {reason}',
  'xr.check': 'প্রোভাইডারে দেখুন',
  'xr.refresh': 'আবার দেখুন',
  'xr.cached': 'সংরক্ষিত উত্তর এখনো হালনাগাদ।',
  'xr.skipped': 'দেখা হয়নি: {reason}।',
  'xr.fact.DELIVERED_PARCELS': 'ডেলিভারি হওয়া পার্সেল',
  'xr.fact.RETURNED_PARCELS': 'ফেরত আসা পার্সেল',
  'xr.fact.CANCELLED_ORDERS': 'বাতিল অর্ডার',
  'xr.fact.TOTAL_PARCELS': 'সব পার্সেল',
  'xr.err.PROVIDER_TIMEOUT': 'প্রোভাইডার অনেক দেরি করেছে',
  'xr.err.PROVIDER_UNAVAILABLE': 'প্রোভাইডার এখন পাওয়া যাচ্ছে না',
  'xr.err.PROVIDER_RATE_LIMITED': 'প্রোভাইডারে অনেক বেশি অনুরোধ',
  'xr.err.PROVIDER_AUTH_FAILED': 'প্রোভাইডার ক্রেডেনশিয়াল গ্রহণ করেনি',
  'xr.err.PROVIDER_REJECTED': 'প্রোভাইডার অনুরোধটি ফিরিয়ে দিয়েছে',
  'xr.err.PROVIDER_BAD_RESPONSE': 'প্রোভাইডারের উত্তর পড়া যায়নি',
  'xr.err.NEW_ORDER_REQUIRED':
      'এই প্রোভাইডার শুধু নতুন অর্ডার (গত ৩ দিনের খসড়া বা কনফার্ম) থাকা গ্রাহককে চেক করে',
  'xr.net.title': 'নেটওয়ার্কের প্রেক্ষাপট',
  'xr.net.hint':
      'সম্মত শপগুলোর মাসিক বেনামি হিসাব। নেটওয়ার্কের কথা, এই গ্রাহকের নয়।',
  'xr.net.none': 'এখনো কোনো বেনামি হিসাব প্রকাশ হয়নি।',
  'xr.net.insufficient': 'যথেষ্ট তথ্য নেই',
  'xr.net.RTO_RATE': 'নেটওয়ার্কে ফেরতের (RTO) হার',
  'xr.net.DELIVERY_SUCCESS': 'নেটওয়ার্কে ডেলিভারি সফলতা',
  'xr.net.period': '{period} মাস (UTC)',
};
