'use client';

/**
 * English and Bangla for the dashboard.
 *
 * The wording follows the mobile app's: where the same business concept appears
 * in both, it is said the same way, because a seller who learns "COD outstanding"
 * on their phone should not meet a different phrase on the desktop. Provider and
 * technical terms stay English in both languages — COD, SKU, CSV, Pathao, RedX,
 * Steadfast — exactly as they do on mobile.
 */

export type Locale = 'en' | 'bn';

export const strings = {
  en: {
    'app.name': 'ecomsbd',
    'app.tagline': 'Seller workstation',

    'nav.dashboard': 'Dashboard',
    'nav.orders': 'Orders',
    'nav.customers': 'Customers',
    'nav.products': 'Products',
    'nav.couriers': 'Couriers',
    'nav.money': 'Money',
    'nav.reconciliation': 'Reconciliation',
    'nav.imports': 'Imports',
    'nav.team': 'Team',
    'nav.settings': 'Settings',
    'nav.operations': 'Operations',
    'nav.finance': 'Finance',
    'nav.shop': 'Shop',

    'auth.title': 'Sign in',
    'auth.subtitle': 'Use the email and password for your ecomsbd account.',
    'auth.email': 'Email',
    'auth.password': 'Password',
    'auth.signIn': 'Sign in',
    'auth.signingIn': 'Signing in…',
    'auth.signOut': 'Sign out',
    'auth.failed': 'That email and password did not match.',
    'auth.needed': 'Please sign in to continue.',

    'common.loading': 'Loading…',
    'common.retry': 'Try again',
    'common.search': 'Search',
    'common.filter': 'Filter',
    'common.all': 'All',
    'common.none': 'None',
    'common.close': 'Close',
    'common.cancel': 'Cancel',
    'common.next': 'Next',
    'common.previous': 'Previous',
    'common.selected': 'selected',
    'common.couldNotLoad': 'Could not load this.',
    'common.nothingHere': 'Nothing here yet',
    'common.language': 'Language',

    'orders.title': 'Orders',
    'orders.subtitle': 'Every order in your shop, newest first.',
    'orders.searchHint': 'Order number, customer name or phone',
    'orders.order': 'Order',
    'orders.customer': 'Customer',
    'orders.amount': 'Amount',
    'orders.courier': 'Courier',
    'orders.delivery': 'Delivery status',
    'orders.cod': 'COD',
    'orders.risk': 'Risk',
    'orders.profit': 'Profit',
    'orders.actions': 'Actions',
    'orders.view': 'View',
    'orders.empty': 'No orders match this view.',
    'orders.emptyHint': 'Clear the filters, or take your first order on the phone.',
    'orders.bulkBook': 'Book with courier',
    'orders.bulkHint': 'Only orders that can be booked are sent.',
    'orders.detail': 'Order detail',
    'orders.items': 'Items',
    'orders.noCourier': 'Not booked',

    'dash.title': 'Dashboard',
    'dash.subtitle': 'Where your shop stands right now.',
    'dash.ordersToday': 'Orders today',
    'dash.codOutstanding': 'COD outstanding',
    'dash.codOutstandingHint': 'Collected by couriers, not yet paid to you.',
    'dash.awaitingPayout': 'Awaiting payout',
    'dash.returns': 'Returns',
    'dash.profit': 'Profit',
    'dash.alerts': 'Needs your attention',
    'dash.allClear': 'Nothing needs you right now.',

    'err.forbidden': 'Your role does not allow this.',
    'err.offline': 'Could not reach the server.',
  },

  bn: {
    'app.name': 'ecomsbd',
    'app.tagline': 'সেলার ওয়ার্কস্টেশন',

    'nav.dashboard': 'ড্যাশবোর্ড',
    'nav.orders': 'অর্ডার',
    'nav.customers': 'কাস্টমার',
    'nav.products': 'পণ্য',
    'nav.couriers': 'কুরিয়ার',
    'nav.money': 'টাকা',
    'nav.reconciliation': 'মিলিয়ে দেখা',
    'nav.imports': 'ইমপোর্ট',
    'nav.team': 'টিম',
    'nav.settings': 'সেটিংস',
    'nav.operations': 'পরিচালনা',
    'nav.finance': 'হিসাব',
    'nav.shop': 'দোকান',

    'auth.title': 'সাইন ইন',
    'auth.subtitle': 'আপনার ecomsbd অ্যাকাউন্টের ইমেইল ও পাসওয়ার্ড দিন।',
    'auth.email': 'ইমেইল',
    'auth.password': 'পাসওয়ার্ড',
    'auth.signIn': 'সাইন ইন',
    'auth.signingIn': 'সাইন ইন হচ্ছে…',
    'auth.signOut': 'সাইন আউট',
    'auth.failed': 'এই ইমেইল ও পাসওয়ার্ড মেলেনি।',
    'auth.needed': 'চালিয়ে যেতে সাইন ইন করুন।',

    'common.loading': 'লোড হচ্ছে…',
    'common.retry': 'আবার চেষ্টা করুন',
    'common.search': 'খুঁজুন',
    'common.filter': 'ফিল্টার',
    'common.all': 'সব',
    'common.none': 'কিছু না',
    'common.close': 'বন্ধ করুন',
    'common.cancel': 'বাতিল',
    'common.next': 'পরের',
    'common.previous': 'আগের',
    'common.selected': 'বাছাই করা',
    'common.couldNotLoad': 'এটি লোড করা যায়নি।',
    'common.nothingHere': 'এখনও কিছু নেই',
    'common.language': 'ভাষা',

    'orders.title': 'অর্ডার',
    'orders.subtitle': 'আপনার দোকানের সব অর্ডার, নতুনগুলো আগে।',
    'orders.searchHint': 'অর্ডার নম্বর, কাস্টমারের নাম বা ফোন',
    'orders.order': 'অর্ডার',
    'orders.customer': 'কাস্টমার',
    'orders.amount': 'টাকার অঙ্ক',
    'orders.courier': 'কুরিয়ার',
    'orders.delivery': 'ডেলিভারি স্টেটাস',
    'orders.cod': 'COD',
    'orders.risk': 'ঝুঁকি',
    'orders.profit': 'লাভ',
    'orders.actions': 'কাজ',
    'orders.view': 'দেখুন',
    'orders.empty': 'এই ভিউতে কোনো অর্ডার নেই।',
    'orders.emptyHint': 'ফিল্টার সরিয়ে দেখুন, অথবা ফোন থেকে প্রথম অর্ডার নিন।',
    'orders.bulkBook': 'কুরিয়ারে বুক করুন',
    'orders.bulkHint': 'যেগুলো বুক করা যাবে শুধু সেগুলোই পাঠানো হবে।',
    'orders.detail': 'অর্ডারের বিস্তারিত',
    'orders.items': 'পণ্য',
    'orders.noCourier': 'বুক হয়নি',

    'dash.title': 'ড্যাশবোর্ড',
    'dash.subtitle': 'আপনার দোকান এখন কোথায় দাঁড়িয়ে।',
    'dash.ordersToday': 'আজকের অর্ডার',
    'dash.codOutstanding': 'বকেয়া COD',
    'dash.codOutstandingHint': 'কুরিয়ার তুলেছে, আপনাকে এখনও দেয়নি।',
    'dash.awaitingPayout': 'পেআউটের অপেক্ষায়',
    'dash.returns': 'ফেরত',
    'dash.profit': 'লাভ',
    'dash.alerts': 'আপনার নজর দরকার',
    'dash.allClear': 'এখন কিছুরই দরকার নেই।',

    'err.forbidden': 'আপনার দায়িত্বে এটি করা যায় না।',
    'err.offline': 'সার্ভারে পৌঁছানো যায়নি।',
  },
} as const;

export type StringKey = keyof (typeof strings)['en'];

export function translate(
  locale: Locale,
  key: StringKey,
  vars?: Record<string, string | number>,
): string {
  const table = strings[locale] ?? strings.en;
  // Falling back to English rather than showing the key: a missing Bangla
  // string should read as English, not as `orders.title`.
  let value: string = table[key] ?? strings.en[key] ?? key;
  if (vars) {
    for (const [name, replacement] of Object.entries(vars)) {
      value = value.replaceAll(`{${name}}`, String(replacement));
    }
  }
  return value;
}
