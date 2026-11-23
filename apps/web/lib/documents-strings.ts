/** Invoice and label printing copy (V4 WP0.5). */
export const documentsEn = {
  'doc.printInvoice': 'Print invoice',
  'doc.printLabel': 'Print label',
  'doc.labelSize': 'Label size',
  'doc.size.4x6': '4 × 6 in',
  'doc.size.3x4': '3 × 4 in',
  'doc.size.a4-8up': 'A4, 8 per page',
  'doc.maskPhone': 'Hide the customer phone on the label',
  'doc.printLabels': 'Print labels',
  'doc.printInvoices': 'Print invoices',
  'doc.notEnabled': 'Printing invoices and labels is not turned on for your shop yet.',
  'doc.tooMany': 'Up to 100 orders can be printed at once.',
} as const;

export const documentsBn: Record<keyof typeof documentsEn, string> = {
  'doc.printInvoice': 'চালান প্রিন্ট করুন',
  'doc.printLabel': 'লেবেল প্রিন্ট করুন',
  'doc.labelSize': 'লেবেলের সাইজ',
  'doc.size.4x6': '4 × 6 ইঞ্চি',
  'doc.size.3x4': '3 × 4 ইঞ্চি',
  'doc.size.a4-8up': 'A4, এক পাতায় 8টি',
  'doc.maskPhone': 'লেবেলে কাস্টমারের ফোন লুকান',
  'doc.printLabels': 'লেবেল প্রিন্ট করুন',
  'doc.printInvoices': 'চালান প্রিন্ট করুন',
  'doc.notEnabled': 'আপনার শপে চালান ও লেবেল প্রিন্ট এখনো চালু হয়নি।',
  'doc.tooMany': 'একবারে সর্বোচ্চ 100টি অর্ডার প্রিন্ট করা যায়।',
};
