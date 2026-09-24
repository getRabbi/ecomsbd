/// Forecasting copy for the phone (V3.6): what is likely to run out, what to
/// reorder, and the cash outlook. Every figure is labelled as an estimate.
library;

const forecastingEn = <String, String>{
  'fc.title': 'Forecasts',
  'fc.tab.reorder': 'Reorder',
  'fc.tab.cash': 'Cash outlook',
  'fc.hint': 'Estimates from your own sales, never a guarantee.',
  'fc.empty': 'Nothing is likely to run out right now.',
  'fc.rate': '{rate}/day',
  'fc.onHand': 'On hand {n}',
  'fc.incoming': 'On order {n}',
  'fc.runsOut': 'Runs out in about {n} days',
  'fc.runsOutNow': 'Out of stock now',
  'fc.suggested': 'Reorder {n}',
  'fc.lead': 'Arrives in about {n} days',
  'fc.conf.HIGH': 'High confidence',
  'fc.conf.MEDIUM': 'Medium confidence',
  'fc.conf.INSUFFICIENT': 'Not enough history',
  'fc.draft': 'Draft PO',
  'fc.drafted': 'Draft {number} created. Review it on web before ordering.',
  'fc.err.NO_PREFERRED_SUPPLIER':
      'Set a preferred supplier for this item first.',
  'fc.err.ALREADY_ON_ORDER': 'Already on a draft or open purchase order.',
  'fc.err.NO_SUGGESTION': 'Nothing to reorder right now.',
  'fc.cash.hint':
      'Expected COD in against supplier money due out. An estimate, not a bank balance.',
  'fc.cash.net7': 'Net, next 7 days',
  'fc.cash.net14': 'Net, next 14 days',
  'fc.cash.in7': 'COD expected, next 7 days',
  'fc.cash.out7': 'Supplier payments due, next 7 days',
  'fc.cash.overdue': 'Supplier payments overdue',
  'fc.cash.committed': 'Spoken for on open orders',
  'fc.cash.noAccess': 'The cash outlook needs money access.',
};

const forecastingBn = <String, String>{
  'fc.title': 'পূর্বাভাস',
  'fc.tab.reorder': 'আবার অর্ডার',
  'fc.tab.cash': 'টাকার অনুমান',
  'fc.hint': 'আপনার নিজের বিক্রি থেকে অনুমান, নিশ্চয়তা নয়।',
  'fc.empty': 'এখন কোনো মাল ফুরিয়ে যাওয়ার আশঙ্কা নেই।',
  'fc.rate': 'দিনে {rate}',
  'fc.onHand': 'হাতে {n}',
  'fc.incoming': 'অর্ডারে {n}',
  'fc.runsOut': 'প্রায় {n} দিনে ফুরাবে',
  'fc.runsOutNow': 'এখন স্টক নেই',
  'fc.suggested': 'আনুন {n}টি',
  'fc.lead': 'আসতে প্রায় {n} দিন',
  'fc.conf.HIGH': 'নির্ভরযোগ্যতা বেশি',
  'fc.conf.MEDIUM': 'নির্ভরযোগ্যতা মাঝারি',
  'fc.conf.INSUFFICIENT': 'যথেষ্ট ইতিহাস নেই',
  'fc.draft': 'খসড়া অর্ডার',
  'fc.drafted':
      'খসড়া {number} তৈরি হয়েছে। অর্ডার দেওয়ার আগে ওয়েবে দেখে নিন।',
  'fc.err.NO_PREFERRED_SUPPLIER': 'আগে এই পণ্যের পছন্দের সরবরাহকারী ঠিক করুন।',
  'fc.err.ALREADY_ON_ORDER': 'আগে থেকেই খসড়া বা চলমান ক্রয় আদেশে আছে।',
  'fc.err.NO_SUGGESTION': 'এখন আবার আনার দরকার নেই।',
  'fc.cash.hint':
      'যত COD আসার কথা আর সরবরাহকারীকে যত দিতে হবে। অনুমান, ব্যাংকের হিসাব নয়।',
  'fc.cash.net7': 'নিট, আগামী ৭ দিন',
  'fc.cash.net14': 'নিট, আগামী ১৪ দিন',
  'fc.cash.in7': 'COD আসার কথা, আগামী ৭ দিন',
  'fc.cash.out7': 'সরবরাহকারীকে দিতে হবে, আগামী ৭ দিন',
  'fc.cash.overdue': 'সরবরাহকারীর বাকি, সময় পেরিয়েছে',
  'fc.cash.committed': 'চলমান অর্ডারে ধরা আছে',
  'fc.cash.noAccess': 'টাকার অনুমান দেখতে টাকার হিসাবের অনুমতি লাগবে।',
};
