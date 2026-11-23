/// Copy for paying by bKash / Nagad with a TrxID (V4 WP2.5).
///
/// Same meaning as the web's `mp.*` strings (`apps/web/lib/manual-payment.ts`).
library;

const manualPaymentEn = <String, String>{
  'mp.title': 'Pay by bKash or Nagad',
  'mp.entry': 'Send the plan price by bKash or Nagad and enter the TrxID.',
  'mp.intro':
      'Send the amount below with Send Money, then enter the TrxID from your '
      'bKash or Nagad app. We check it and turn your plan on, usually within '
      'a day.',
  'mp.plan': 'Plan',
  'mp.months': 'Months',
  'mp.monthsValue': '{count} month(s)',
  'mp.amount': 'Amount to send',
  'mp.sendTo': 'Send to',
  'mp.method': 'Paid with',
  'mp.payer': 'Number you sent from',
  'mp.trx': 'TrxID',
  'mp.trxHint':
      'The transaction ID in the bKash / Nagad confirmation message, '
      'e.g. 8N7A6D5C4B.',
  'mp.submit': 'Submit payment',
  'mp.sending': 'Submitting…',
  'mp.invalidPayer': 'Enter the 11-digit mobile number you sent from.',
  'mp.invalidTrx': 'A TrxID is 6 to 20 letters and digits.',
  'mp.sent': 'Submitted. We will check the TrxID and let you know.',
  'mp.history': 'Your payments',
  'mp.state.SUBMITTED': 'Waiting for check',
  'mp.state.APPROVED': 'Approved',
  'mp.state.REJECTED': 'Not approved',
  'mp.until': 'Plan active until {date}',
  'mp.why': 'Reason: {reason}',
  'mp.errUsed': 'This TrxID has already been submitted.',
  'mp.errTrx': 'A TrxID is 6 to 20 letters and digits.',
  'mp.errUnavailable': 'Paying by bKash or Nagad is not available right now.',
  'mp.bkash': 'bKash',
  'mp.nagad': 'Nagad',
  'mp.loadFailed': 'Could not load the payment details.',
};

const manualPaymentBn = <String, String>{
  'mp.title': 'বিকাশ বা নগদে পেমেন্ট',
  'mp.entry': 'প্ল্যানের টাকা বিকাশ বা নগদে পাঠিয়ে TrxID লিখুন।',
  'mp.intro':
      'নিচের টাকা Send Money করে পাঠান, তারপর বিকাশ বা নগদ অ্যাপে পাওয়া TrxID '
      'লিখুন। আমরা যাচাই করে সাধারণত এক দিনের মধ্যে প্ল্যান চালু করে দিই।',
  'mp.plan': 'প্ল্যান',
  'mp.months': 'মাস',
  'mp.monthsValue': '{count} মাস',
  'mp.amount': 'যত টাকা পাঠাবেন',
  'mp.sendTo': 'এই নম্বরে পাঠান',
  'mp.method': 'যেভাবে পাঠিয়েছেন',
  'mp.payer': 'যে নম্বর থেকে পাঠিয়েছেন',
  'mp.trx': 'TrxID',
  'mp.trxHint':
      'বিকাশ / নগদের কনফার্মেশন মেসেজে থাকা ট্রানজেকশন আইডি, যেমন 8N7A6D5C4B।',
  'mp.submit': 'পেমেন্ট জমা দিন',
  'mp.sending': 'জমা হচ্ছে…',
  'mp.invalidPayer': 'যে ১১ সংখ্যার মোবাইল নম্বর থেকে পাঠিয়েছেন সেটি লিখুন।',
  'mp.invalidTrx': 'TrxID ৬ থেকে ২০টি ইংরেজি অক্ষর ও সংখ্যা হয়।',
  'mp.sent': 'জমা হয়েছে। আমরা TrxID যাচাই করে আপনাকে জানাব।',
  'mp.history': 'আপনার পেমেন্ট',
  'mp.state.SUBMITTED': 'যাচাইয়ের অপেক্ষায়',
  'mp.state.APPROVED': 'গ্রহণ করা হয়েছে',
  'mp.state.REJECTED': 'গ্রহণ করা হয়নি',
  'mp.until': 'প্ল্যান চালু থাকবে {date} পর্যন্ত',
  'mp.why': 'কারণ: {reason}',
  'mp.errUsed': 'এই TrxID আগেই জমা দেওয়া হয়েছে।',
  'mp.errTrx': 'TrxID ৬ থেকে ২০টি ইংরেজি অক্ষর ও সংখ্যা হয়।',
  'mp.errUnavailable': 'এখন বিকাশ বা নগদে পেমেন্ট নেওয়া যাচ্ছে না।',
  'mp.bkash': 'বিকাশ',
  'mp.nagad': 'নগদ',
  'mp.loadFailed': 'পেমেন্টের তথ্য আনা যায়নি।',
};
