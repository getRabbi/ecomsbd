// The public ecomsbd.com site: every word, link and sample figure on it.
//
// Paired translations, like the legal pages, so the two languages cannot drift
// apart. Each line is a public claim about the product: say only what ships
// today, and keep anything gated behind a "Coming soon" label.

export type Locale = 'en' | 'bn';
export type Tone = 'good' | 'warn' | 'bad' | 'neutral';

export const SITE_URL = 'https://ecomsbd.com';
const APP_URL = 'https://app.ecomsbd.com';

/**
 * Where the site sends people. The workspace has one auth page: `/sign-in`
 * holds both "Sign in" and "Create account", so both CTAs land there. The
 * workspace root sends a signed-in seller to the dashboard and anyone else to
 * sign-in.
 */
export const links = {
  start: `${APP_URL}/sign-in`,
  signIn: `${APP_URL}/sign-in`,
  app: APP_URL,
  dashboard: `${APP_URL}/dashboard`,
  play: 'https://play.google.com/store/apps/details?id=com.ecomsbd.app',
  support: '/ecomsbd-support',
} as const;

/** Absolute so the language switch always lands on the canonical site. */
export const homeUrl: Record<Locale, string> = { en: `${SITE_URL}/`, bn: `${SITE_URL}/bn` };
export const privacyPath: Record<Locale, string> = { en: '/privacy-policy', bn: '/privacy-policy/bn' };
export const deletionPath: Record<Locale, string> = { en: '/account-deletion', bn: '/account-deletion/bn' };

/** In-page sections the header links to; each must exist as an element id. */
export const sectionIds = {
  features: 'features',
  how: 'how-it-works',
  webMobile: 'web-mobile',
  couriers: 'couriers',
  faq: 'faq',
} as const;

export const COURIERS = ['Steadfast', 'Pathao', 'RedX'] as const;

// Sample shop for the product previews. Amounts are paisa, as the API sends
// them; the page formats them the way the workspace does.
export const sample = {
  kpis: [4825000, 37, 12640000, 987000],
  orders: [
    { id: '#1048', amount: 185000, status: 0, parcel: 0, courier: null },
    { id: '#1047', amount: 240000, status: 1, parcel: 1, courier: 'Pathao' },
    { id: '#1046', amount: 125000, status: 2, parcel: 2, courier: 'Steadfast' },
    { id: '#1045', amount: 310000, status: 1, parcel: 3, courier: 'RedX' },
  ],
  // The desktop orders preview: the Confirmed queue, first three selected.
  queue: [145000, 290000, 98000, 172000, 185000],
  attention: [
    { tone: 'warn', amount: 124000, count: 2 },
    { tone: 'bad', amount: 860000, count: null },
    { tone: 'warn', amount: null, count: 3 },
  ],
  // Delivery and COD charges are deducted by the courier before it pays out.
  payout: { cod: 6245000, delivery: -418000, codCharge: -62000, expected: 5765000, paid: 5641000, difference: -124000, parcels: 28, matched: 26, charge: 1, missing: 1 },
  insights: { sales: 48230000, change: 12, profit: 7190000, rto: 8.4, rtoCount: 21, completed: 250, receivable: 12640000, overdue: 860000, low: 4, out: 1 },
  // Profit per day over the last two weeks, in taka.
  trend: [1900, 2300, 2100, 2800, 2500, 3100, 2900, 2600, 3400, 3200, 3700, 3500, 4100, 3900],
  products: [64, 41, 27],
} as const;

export const statusTone: Tone[] = ['neutral', 'neutral', 'good'];
export const parcelTone: Tone[] = ['neutral', 'neutral', 'good', 'warn'];

const en = {
  meta: {
    title: 'ecomsbd — Seller operations for online sellers in Bangladesh',
    description:
      'Orders, customers, courier booking, COD tracking, payout reconciliation, inventory and profit in one place for Bangladesh’s online and F-commerce sellers. Free during launch.',
    ogAlt: 'ecomsbd — run your online business from one place',
  },
  skip: 'Skip to content',
  brandTagline: 'Seller operations',
  nav: {
    label: 'Main',
    features: 'Features',
    how: 'How it works',
    couriers: 'Couriers',
    webMobile: 'Web & Mobile',
    faq: 'FAQ',
    support: 'Support',
    menu: 'Open menu',
    otherLanguage: 'বাংলা',
    otherLanguageLabel: 'বাংলায় দেখুন',
  },
  cta: {
    start: 'Start free',
    signIn: 'Sign in',
    openApp: 'Open web app',
    openDashboard: 'Open dashboard',
    playTop: 'Download for Android',
    playBottom: 'Google Play',
  },
  hero: {
    eyebrow: 'For online & F-commerce sellers in Bangladesh',
    title: 'Run your online business',
    titleAccent: 'from one place.',
    lead: 'Orders, customers, courier delivery, COD payments, inventory and profit — managed together for Bangladesh’s online sellers.',
    points: ['Free during launch', 'No card needed', 'Browser + Android app'],
    hint: 'New here? Choose “Create account” on the sign-in page, or continue with Google or Apple.',
    caption: 'Sample shop with illustrative figures.',
  },
  heroUi: {
    alt: 'Preview of the ecomsbd dashboard: today’s sales, orders, COD outstanding, contribution profit, recent orders with courier status, and items that need attention.',
    shop: 'Nakshi Fashion',
    nav: ['Dashboard', 'Orders', 'Customers', 'Products', 'Couriers', 'Money', 'Reconciliation', 'Insights'],
    title: 'Dashboard',
    subtitle: 'Where your shop stands right now.',
    today: 'Today',
    estimate: 'Includes estimates',
    kpis: ['Gross sales', 'Orders today', 'COD outstanding', 'Contribution profit'],
    recent: 'Recent orders',
    columns: ['Order', 'Customer', 'Amount', 'Status', 'Delivery'],
    customers: [
      ['Nusrat Jahan', 'Mirpur, Dhaka'],
      ['Rakib Hasan', 'Agrabad, Chattogram'],
      ['Farhana Akter', 'Zindabazar, Sylhet'],
      ['Tanvir Ahmed', 'Uttara, Dhaka'],
    ],
    statuses: ['Confirmed', 'With courier', 'Completed'],
    parcels: ['Not booked', 'On its way', 'Delivered', 'Coming back'],
    attentionTitle: 'Needs your attention',
    attention: ['{count} payout mismatches · {amount}', 'COD overdue · {amount}', '{count} products low on stock'],
  },
  strip: [
    { title: 'Orders in one place', body: 'Create, confirm and follow every order.' },
    { title: 'Bangladesh courier workflow', body: 'Steadfast, Pathao and RedX from your own accounts.' },
    { title: 'COD tracking', body: 'See what is owed, paid and overdue.' },
    { title: 'Real profit visibility', body: 'Profit after courier charges, costs and returns.' },
    { title: 'Inventory control', body: 'Stock, variants and low-stock alerts.' },
    { title: 'Desktop + mobile', body: 'Full workspace in the browser, Android app on the go.' },
  ],
  features: {
    eyebrow: 'Features',
    title: 'Everything between the order and the payout',
    lead: 'ecomsbd is organised around how an online shop actually works — from the first customer message to the money in your account.',
    groups: [
      {
        title: 'Orders & customers',
        summary: 'Capture orders quickly and know who you are shipping to.',
        items: [
          'Create, confirm and update orders in one list',
          'Paste a customer’s message and ecomsbd detects the order details',
          'Repeat-order warning for a similar order from the same number',
          'Customer history: past orders, deliveries and returns',
          'Customer notes, tags and follow-ups',
        ],
      },
      {
        title: 'Delivery & couriers',
        summary: 'Book and follow parcels without retyping addresses.',
        items: [
          'Connect your Steadfast, Pathao and RedX accounts',
          'Book a parcel straight from the order',
          'Bulk-book confirmed orders in one go',
          'Follow delivery status on every order',
          'Compare what each courier supports',
        ],
      },
      {
        title: 'COD & money',
        summary: 'Know where every taka of cash on delivery is.',
        items: [
          'COD receivables by order and by courier',
          'Payouts tracked against what was expected',
          'Reconcile courier statements (CSV or Excel)',
          'Discrepancies stay open until they are resolved',
          'Expenses and cashflow',
        ],
      },
      {
        title: 'Products & inventory',
        summary: 'Keep stock right without a separate spreadsheet.',
        items: [
          'Product catalogue with variants and SKUs',
          'Stock adjustments and restocks',
          'Movement history for every item',
          'Low-stock and out-of-stock alerts',
        ],
      },
      {
        title: 'Insights',
        summary: 'See what is working — and what is costing you.',
        items: [
          'Sales and orders over time',
          'Contribution profit, marked estimated or final',
          'RTO rate and return loss',
          'Courier and product performance',
          'Customer and inventory insights',
        ],
      },
      {
        title: 'Business operations',
        summary: 'The back-office work around the orders.',
        items: [
          'Returns and RTO handling',
          'Procurement: suppliers and purchase orders',
          'Stock forecasting',
          'Spreadsheet imports for orders and products',
          'Team members with roles and permissions',
          'Notifications, automation rules and email campaigns',
        ],
      },
    ],
  },
  how: {
    eyebrow: 'How it works',
    title: 'From the first message to real profit',
    lead: 'Every order goes through the same seven steps. ecomsbd keeps them connected, so nothing falls through the gaps.',
    phases: [
      {
        label: 'Sell',
        steps: [
          { title: 'Capture the order', body: 'Type it in, or paste the customer’s message and let ecomsbd pick out name, phone, address and items.' },
          { title: 'Review & confirm', body: 'Check the details and any repeat-order warning, then confirm.' },
        ],
      },
      {
        label: 'Deliver',
        steps: [
          { title: 'Book the courier', body: 'Send one order or a whole batch to Steadfast, Pathao or RedX.' },
          { title: 'Track delivery', body: 'Follow each parcel’s status on the order, including returns.' },
        ],
      },
      {
        label: 'Get paid',
        steps: [
          { title: 'Track COD', body: 'See how much cash on delivery each courier owes you, and what is overdue.' },
          { title: 'Reconcile the payout', body: 'Match what arrived against what was expected, line by line.' },
        ],
      },
      {
        label: 'Learn',
        steps: [{ title: 'Understand performance', body: 'Profit, RTO, couriers and products — from your own figures.' }],
      },
    ],
  },
  desktop: {
    eyebrow: 'Web workspace',
    title: 'A complete workspace for the laptop on your shop table',
    lead: 'Most serious sellers run their business from a laptop or PC. ecomsbd’s browser workspace puts the whole operation on one big screen, with nothing to install.',
    items: [
      { title: 'Full dashboard', body: 'Today’s sales, COD and what needs attention.' },
      { title: 'Dense order tables', body: 'Scan dozens of orders and filter by status.' },
      { title: 'Bulk workflows', body: 'Select orders and book them together.' },
      { title: 'Inventory', body: 'Products, variants, stock and movements.' },
      { title: 'Money & reconciliation', body: 'Receivables, payouts and statements.' },
      { title: 'Analytics', body: 'Profit, RTO, couriers and products.' },
      { title: 'Procurement', body: 'Suppliers, purchase orders and forecasts.' },
      { title: 'Team & settings', body: 'Invite staff and decide who can do what.' },
    ],
    ui: {
      alt: 'Preview of the ecomsbd orders table in the browser, with three orders selected for courier booking.',
      title: 'Orders',
      tabs: ['All', 'Confirmed', 'With courier', 'Delivered', 'Returns'],
      selected: '3 selected',
      book: 'Book with courier',
      hint: 'Only orders that can be booked are sent.',
      columns: ['Order', 'Customer', 'Area', 'Amount', 'Status'],
      rows: [
        ['#1052', 'Sadia Islam', 'Dhanmondi, Dhaka'],
        ['#1051', 'Mehedi Hasan', 'Khulna Sadar'],
        ['#1050', 'Ayesha Siddika', 'Rajshahi Sadar'],
        ['#1049', 'Imran Kabir', 'Cumilla Sadar'],
        ['#1048', 'Nusrat Jahan', 'Mirpur, Dhaka'],
      ],
    },
  },
  mobile: {
    eyebrow: 'Android app',
    title: 'Your business doesn’t stop when you leave the desk',
    lead: 'The ecomsbd Android app is the companion to the web workspace: the same shop and the same orders, in your pocket.',
    items: [
      'Notifications for deliveries, returns, payouts and stock',
      'Check an order or a customer in seconds',
      'Courier status while you are out',
      'Create, confirm and book orders on the go',
      'Today’s sales and COD at a glance',
    ],
    ios: 'iPhone app: coming soon.',
    ui: {
      alt: 'Preview of the ecomsbd Android app showing today’s figures and recent notifications.',
      greeting: 'Today',
      sales: 'Gross sales',
      cod: 'COD outstanding',
      notifications: 'Notifications',
      items: [
        { title: 'Parcel delivered', meta: '#1046 · Steadfast' },
        { title: 'Payout mismatch found', meta: 'Pathao · {amount}' },
        { title: 'Low stock', meta: 'Cotton three-piece (M) · {count} left' },
        { title: 'Parcel coming back', meta: '#1045 · RedX' },
      ],
    },
  },
  together: {
    title: 'One shop. Two screens. The same numbers.',
    lead: 'Sign in with the same account on the web and on Android. Both work on the same shop and the same ecomsbd data, so there is nothing to sync and no second account.',
    cols: [
      { title: 'Desktop', body: 'Deep work and complete operations: bulk booking, reconciliation, inventory, reports and team settings.' },
      { title: 'Mobile', body: 'Alerts and quick operations when you are away from the desk.' },
      { title: 'Both', body: 'One account, one shop, one set of business figures.' },
    ],
  },
  couriers: {
    eyebrow: 'Couriers',
    title: 'Connect your courier accounts',
    lead: 'Keep the courier accounts and rates you already have. ecomsbd works with them, so you stop copying addresses between tabs.',
    active: 'Active',
    via: 'Through your own account',
    capabilities: [
      { title: 'Connect your account', body: 'Add your courier credentials. They are stored encrypted and only ever shown back masked.' },
      { title: 'Book parcels', body: 'Book one order, or bulk-book a batch, without retyping addresses.' },
      { title: 'Track status', body: 'Follow each parcel’s delivery status from the order.' },
      { title: 'COD workflow', body: 'Expected COD, charges and payouts per courier, ready to reconcile.' },
      { title: 'Compare couriers', body: 'See what each courier has been verified to do with ecomsbd.' },
    ],
    note: 'Delivery prices come from your own courier account. Courier names belong to their owners; ecomsbd connects to the accounts you hold with them.',
  },
  money: {
    eyebrow: 'COD & reconciliation',
    title: 'Selling isn’t finished when the parcel is delivered',
    lead: 'Cash on delivery travels through your courier before it reaches you. ecomsbd follows that money until the payout is matched.',
    items: [
      { title: 'COD that should arrive', body: 'Receivables per order and per courier, including what is overdue.' },
      { title: 'What was actually paid', body: 'Record payouts, or upload the courier’s statement as CSV or Excel.' },
      { title: 'Payout differences', body: 'Expected and paid amounts compared, line by line.' },
      { title: 'Courier deductions', body: 'Delivery and COD charges shown separately, so unknown deductions stand out.' },
      { title: 'Open discrepancies', body: 'Mismatches stay open as cases until you resolve them.' },
      { title: 'Contribution profit', body: 'Profit after product cost, courier charges and returns.' },
    ],
    note: 'Where a cost or charge is still missing, profit is labelled as an estimate — never shown as exact.',
    ui: {
      alt: 'Preview of a courier payout check in ecomsbd: expected COD, courier charges, expected payout, the amount paid and a difference of one thousand two hundred and forty taka.',
      title: 'Payout check',
      courier: 'Steadfast · {count} parcels',
      status: 'Mismatch',
      rows: ['COD collected', 'Delivery charges', 'COD charge', 'Expected payout', 'Paid to you'],
      difference: 'Difference',
      lines: ['Matched', 'Charge mismatch', 'Missing COD'],
      openCase: 'Open case',
    },
  },
  insights: {
    eyebrow: 'Insights',
    title: 'Know what’s actually working',
    lead: 'ecomsbd goes beyond order tracking. Insights are built from your own orders, deliveries, costs and payouts — and say plainly when a figure is still an estimate.',
    items: [
      'Sales and order trends',
      'Profit quality: estimated or final',
      'RTO rate and return loss',
      'Courier performance',
      'Product performance',
      'Cash and COD position',
      'Customer behaviour',
      'Inventory condition',
    ],
    ui: {
      alt: 'Preview of ecomsbd Insights for the last 30 days: delivered sales, profit, RTO rate, outstanding COD, profit over time and top products.',
      title: 'Insights',
      range: '30 days',
      sales: 'Delivered sales',
      vsPrev: '+{change}% vs previous',
      profit: 'Profit',
      estimate: 'Includes estimates',
      rto: 'RTO rate',
      rtoOf: '{rto} of {completed} came back',
      receivable: 'Outstanding COD',
      overdue: '{amount} overdue',
      trend: 'Profit over time',
      perDay: 'Per day',
      top: 'Top products',
      units: '{count} units',
      products: ['Cotton three-piece', 'Printed kurti', 'Kids panjabi'],
      stock: 'Stock alerts',
      stockValue: '{low} low · {out} out',
    },
  },
  soon: {
    eyebrow: 'Coming soon',
    badge: 'Coming soon · শীঘ্রই আসছে',
    title: 'Sales-channel connections are on the way',
    lead: 'We are building automatic order capture from the places you sell. These connections are not available yet.',
    chip: 'Coming soon',
    channels: [
      { name: 'Shopify', body: 'Store orders arriving on their own.' },
      { name: 'WooCommerce', body: 'For WordPress shops.' },
      { name: 'Custom website', body: 'Orders from your own website.' },
      { name: 'Messenger', body: 'Chat orders as drafts to review.' },
      { name: 'WhatsApp', body: 'Orders from WhatsApp Business chats.' },
    ],
    today: 'Today, paste any customer message into ecomsbd and it detects the order details for you.',
  },
  trust: {
    eyebrow: 'Security & trust',
    title: 'Built for real business data',
    lead: 'Your orders, customers and money records belong to your business. ecomsbd keeps each shop separate and makes access deliberate.',
    items: [
      { title: 'Role-based team access', body: 'Invite staff and choose what each role can see and do, money figures included.' },
      { title: 'Shop-separated data', body: 'Each shop’s orders, customers and money records are kept apart from every other shop.' },
      { title: 'Audited sensitive actions', body: 'Important changes are recorded with who made them and when.' },
      { title: 'Secure workspace', body: 'HTTPS throughout and signed-in access with email, Google or Apple.' },
      { title: 'Encrypted credentials', body: 'Courier and connected-service credentials are encrypted and only shown back masked.' },
    ],
    link: 'Read the Privacy Policy',
  },
  free: {
    eyebrow: 'Launch period',
    title: 'Start using ecomsbd free during our launch',
    lead: 'Everything live in ecomsbd is free to use while we launch. Create your shop and start today — there is no checkout and no card to enter.',
    points: [
      'Web workspace and Android app',
      'Steadfast, Pathao and RedX connections',
      'Your team on the same shop',
      'No card or payment details',
    ],
  },
  faq: {
    eyebrow: 'FAQ',
    title: 'Questions sellers ask',
    support: 'Another question? Contact support',
    items: [
      {
        q: 'What is ecomsbd?',
        a: 'ecomsbd is a seller operations platform for online and F-commerce sellers in Bangladesh. It keeps orders, customers, courier bookings, COD money, inventory and profit in one place, so you can run the shop without juggling spreadsheets, courier dashboards and notebooks.',
      },
      {
        q: 'Who is it for?',
        a: 'Sellers who take orders through Facebook pages, chat, phone or their own channels and ship with couriers on cash on delivery — from a solo seller to a small team.',
      },
      {
        q: 'Can I use it from a computer?',
        a: 'Yes. The full workspace runs in your browser at app.ecomsbd.com, on any laptop or PC. There is nothing to install.',
      },
      {
        q: 'Is there an Android app?',
        a: 'Yes. The ecomsbd Android app is on Google Play. It is the companion to the web workspace for notifications, quick checks and orders on the go. An iPhone app is coming soon.',
      },
      {
        q: 'Which couriers are supported?',
        a: 'Steadfast, Pathao and RedX. You connect your own courier accounts, then book parcels, follow delivery status and track COD from ecomsbd.',
      },
      {
        q: 'Can my team use the same shop?',
        a: 'Yes. Invite team members to your shop and give each one a role. The role decides what they can see and do, including whether they see money figures.',
      },
      {
        q: 'Does ecomsbd replace my courier account?',
        a: 'No. You keep your own courier accounts, rates and payouts. ecomsbd connects to them to book and track parcels and to check payouts. Your courier still collects and pays out the cash on delivery; ecomsbd does not collect it.',
      },
      {
        q: 'Is it free right now?',
        a: 'Yes. ecomsbd is free during our launch period and billing is switched off, so there is no checkout and no card or payment details are asked for.',
      },
      {
        q: 'Are Messenger, WhatsApp, Shopify or WooCommerce integrations available?',
        a: 'Not yet. Shopify, WooCommerce, custom website, Messenger and WhatsApp connections are coming soon. Today you can paste a customer’s message and ecomsbd detects the order details for you.',
      },
      {
        q: 'Where is my business data managed?',
        a: 'In your ecomsbd shop, on ecomsbd’s cloud services, separate from every other shop. Some of our cloud providers operate outside Bangladesh, so data may be stored there. The Privacy Policy lists the providers and explains how to delete your account.',
      },
    ],
  },
  final: {
    title: 'Run your seller operations with less guesswork.',
    lead: 'Orders, couriers, COD and profit — connected from the first message to the final payout.',
  },
  footer: {
    tagline: 'Bangladesh-focused seller operations',
    product: 'Product',
    resources: 'Resources',
    legal: 'Legal',
    account: 'Account',
    webApp: 'Web app',
    android: 'Android app',
    privacy: 'Privacy Policy',
    deletion: 'Account deletion',
    rights: 'All rights reserved.',
  },
};

export type SiteCopy = typeof en;

const bn: SiteCopy = {
  meta: {
    title: 'ecomsbd — বাংলাদেশের অনলাইন সেলারদের ব্যবসা পরিচালনা',
    description:
      'অর্ডার, কাস্টমার, কুরিয়ার বুকিং, COD, পেআউট মিলিয়ে দেখা, স্টক আর লাভ — বাংলাদেশের অনলাইন ও এফ-কমার্স সেলারদের জন্য এক জায়গায়। লঞ্চের সময় বিনামূল্যে।',
    ogAlt: 'ecomsbd — আপনার অনলাইন ব্যবসা চালান এক জায়গা থেকে',
  },
  skip: 'মূল অংশে যান',
  brandTagline: 'সেলার অপারেশনস',
  nav: {
    label: 'প্রধান',
    features: 'ফিচার',
    how: 'কীভাবে কাজ করে',
    couriers: 'কুরিয়ার',
    webMobile: 'ওয়েব ও মোবাইল',
    faq: 'প্রশ্নোত্তর',
    support: 'সাপোর্ট',
    menu: 'মেনু খুলুন',
    otherLanguage: 'English',
    otherLanguageLabel: 'View in English',
  },
  cta: {
    start: 'বিনামূল্যে শুরু করুন',
    signIn: 'সাইন ইন',
    openApp: 'ওয়েব অ্যাপ খুলুন',
    openDashboard: 'ড্যাশবোর্ড খুলুন',
    playTop: 'Android-এর জন্য ডাউনলোড',
    playBottom: 'Google Play',
  },
  hero: {
    eyebrow: 'বাংলাদেশের অনলাইন ও এফ-কমার্স সেলারদের জন্য',
    title: 'আপনার অনলাইন ব্যবসা চালান',
    titleAccent: 'এক জায়গা থেকে।',
    lead: 'অর্ডার, কাস্টমার, কুরিয়ার ডেলিভারি, COD পেমেন্ট, স্টক আর লাভ — বাংলাদেশের অনলাইন সেলারদের জন্য সব একসাথে।',
    points: ['লঞ্চের সময় বিনামূল্যে', 'কার্ড লাগবে না', 'ব্রাউজার + Android অ্যাপ'],
    hint: 'নতুন? সাইন-ইন পাতায় “অ্যাকাউন্ট তৈরি করুন” বেছে নিন, অথবা Google বা Apple দিয়ে চালিয়ে যান।',
    caption: 'নমুনা দোকান, উদাহরণের জন্য দেওয়া সংখ্যা।',
  },
  heroUi: {
    alt: 'ecomsbd ড্যাশবোর্ডের নমুনা: আজকের বিক্রি, অর্ডার, বকেয়া COD, কন্ট্রিবিউশন লাভ, কুরিয়ারের অবস্থাসহ সাম্প্রতিক অর্ডার, আর যেগুলোতে নজর দরকার।',
    shop: 'নকশী ফ্যাশন',
    nav: ['ড্যাশবোর্ড', 'অর্ডার', 'কাস্টমার', 'পণ্য', 'কুরিয়ার', 'টাকা', 'মিলিয়ে দেখা', 'বিশ্লেষণ'],
    title: 'ড্যাশবোর্ড',
    subtitle: 'আপনার দোকান এখন কোথায় দাঁড়িয়ে।',
    today: 'আজ',
    estimate: 'আনুমানিক অংশ আছে',
    kpis: ['মোট বিক্রি', 'আজকের অর্ডার', 'বকেয়া COD', 'কন্ট্রিবিউশন লাভ'],
    recent: 'সাম্প্রতিক অর্ডার',
    columns: ['অর্ডার', 'কাস্টমার', 'টাকা', 'অবস্থা', 'ডেলিভারি'],
    customers: [
      ['নুসরাত জাহান', 'মিরপুর, ঢাকা'],
      ['রাকিব হাসান', 'আগ্রাবাদ, চট্টগ্রাম'],
      ['ফারহানা আক্তার', 'জিন্দাবাজার, সিলেট'],
      ['তানভীর আহমেদ', 'উত্তরা, ঢাকা'],
    ],
    statuses: ['নিশ্চিত', 'কুরিয়ারে পাঠানো', 'সম্পন্ন'],
    parcels: ['বুক হয়নি', 'পথে আছে', 'ডেলিভারি হয়েছে', 'ফেরত আসছে'],
    attentionTitle: 'আপনার নজর দরকার',
    attention: ['{count}টি পেআউট অমিল · {amount}', 'মেয়াদ পেরোনো COD · {amount}', '{count}টি পণ্যের স্টক কম'],
  },
  strip: [
    { title: 'সব অর্ডার এক জায়গায়', body: 'প্রতিটি অর্ডার তৈরি, নিশ্চিত আর অনুসরণ করুন।' },
    { title: 'বাংলাদেশের কুরিয়ার ওয়ার্কফ্লো', body: 'নিজের অ্যাকাউন্টে Steadfast, Pathao আর RedX।' },
    { title: 'COD হিসাব', body: 'কত পাওনা, কত এসেছে, কত দেরি — দেখুন।' },
    { title: 'আসল লাভ দেখুন', body: 'কুরিয়ার চার্জ, খরচ আর ফেরতের পরের লাভ।' },
    { title: 'স্টক নিয়ন্ত্রণ', body: 'স্টক, ভ্যারিয়েন্ট আর কম স্টকের সতর্কতা।' },
    { title: 'ডেস্কটপ + মোবাইল', body: 'ব্রাউজারে পুরো ওয়ার্কস্পেস, চলার পথে Android অ্যাপ।' },
  ],
  features: {
    eyebrow: 'ফিচার',
    title: 'অর্ডার থেকে পেআউট — মাঝের সবকিছু',
    lead: 'অনলাইন দোকান আসলে যেভাবে চলে, ecomsbd সেভাবেই সাজানো — কাস্টমারের প্রথম মেসেজ থেকে আপনার অ্যাকাউন্টে টাকা আসা পর্যন্ত।',
    groups: [
      {
        title: 'অর্ডার ও কাস্টমার',
        summary: 'দ্রুত অর্ডার নিন, জেনে নিন কাকে পাঠাচ্ছেন।',
        items: [
          'এক তালিকায় অর্ডার তৈরি, নিশ্চিত ও আপডেট',
          'কাস্টমারের মেসেজ পেস্ট করুন, ecomsbd অর্ডারের তথ্য বের করবে',
          'একই নম্বর থেকে একই রকম অর্ডারে রিপিট-অর্ডার সতর্কতা',
          'কাস্টমারের ইতিহাস: আগের অর্ডার, ডেলিভারি আর ফেরত',
          'কাস্টমার নোট, ট্যাগ আর ফলো-আপ',
        ],
      },
      {
        title: 'ডেলিভারি ও কুরিয়ার',
        summary: 'ঠিকানা আবার না লিখেই পার্সেল বুক ও অনুসরণ।',
        items: [
          'আপনার Steadfast, Pathao ও RedX অ্যাকাউন্ট যুক্ত করুন',
          'অর্ডার থেকেই সরাসরি পার্সেল বুক',
          'নিশ্চিত অর্ডারগুলো একসাথে বাল্ক বুক',
          'প্রতিটি অর্ডারে ডেলিভারির অবস্থা',
          'কোন কুরিয়ার কী সাপোর্ট করে তার তুলনা',
        ],
      },
      {
        title: 'COD ও টাকা',
        summary: 'ক্যাশ অন ডেলিভারির প্রতিটি টাকা কোথায় আছে জানুন।',
        items: [
          'অর্ডার ও কুরিয়ার ধরে COD পাওনা',
          'প্রত্যাশিত টাকার সাথে পেআউট মিলিয়ে দেখা',
          'কুরিয়ারের স্টেটমেন্ট (CSV বা Excel) মিলিয়ে দেখা',
          'সমাধান না হওয়া পর্যন্ত অমিল খোলা থাকে',
          'খরচ ও ক্যাশফ্লো',
        ],
      },
      {
        title: 'পণ্য ও স্টক',
        summary: 'আলাদা স্প্রেডশিট ছাড়াই সঠিক স্টক।',
        items: [
          'ভ্যারিয়েন্ট ও SKU সহ পণ্যের তালিকা',
          'স্টক সমন্বয় ও রিস্টক',
          'প্রতিটি আইটেমের স্টক মুভমেন্টের ইতিহাস',
          'কম স্টক ও স্টক শেষের সতর্কতা',
        ],
      },
      {
        title: 'বিশ্লেষণ',
        summary: 'কী কাজ করছে আর কোথায় খরচ হচ্ছে — দেখুন।',
        items: [
          'সময়ের সাথে বিক্রি ও অর্ডার',
          'কন্ট্রিবিউশন লাভ — আনুমানিক না চূড়ান্ত, চিহ্নিত',
          'RTO হার ও ফেরতে ক্ষতি',
          'কুরিয়ার ও পণ্যের পারফরম্যান্স',
          'কাস্টমার ও স্টকের বিশ্লেষণ',
        ],
      },
      {
        title: 'ব্যবসা পরিচালনা',
        summary: 'অর্ডারের চারপাশের ব্যাক-অফিসের কাজ।',
        items: [
          'রিটার্ন ও RTO সামলানো',
          'ক্রয়: সাপ্লায়ার ও পারচেজ অর্ডার',
          'স্টকের পূর্বাভাস',
          'স্প্রেডশিট থেকে অর্ডার ও পণ্য ইমপোর্ট',
          'রোল ও অনুমতিসহ টিম মেম্বার',
          'নোটিফিকেশন, অটোমেশন নিয়ম ও ইমেইল ক্যাম্পেইন',
        ],
      },
    ],
  },
  how: {
    eyebrow: 'কীভাবে কাজ করে',
    title: 'প্রথম মেসেজ থেকে আসল লাভ পর্যন্ত',
    lead: 'প্রতিটি অর্ডার একই সাতটি ধাপ পেরোয়। ecomsbd ধাপগুলো জুড়ে রাখে, যাতে কিছুই ফাঁকে না পড়ে।',
    phases: [
      {
        label: 'বিক্রি',
        steps: [
          { title: 'অর্ডার নিন', body: 'লিখে দিন, অথবা কাস্টমারের মেসেজ পেস্ট করুন — ecomsbd নাম, ফোন, ঠিকানা আর পণ্য বের করবে।' },
          { title: 'দেখে নিশ্চিত করুন', body: 'তথ্য আর রিপিট-অর্ডার সতর্কতা দেখে নিশ্চিত করুন।' },
        ],
      },
      {
        label: 'ডেলিভারি',
        steps: [
          { title: 'কুরিয়ার বুক করুন', body: 'একটি অর্ডার বা পুরো ব্যাচ পাঠান Steadfast, Pathao বা RedX-এ।' },
          { title: 'ডেলিভারি অনুসরণ', body: 'অর্ডারেই প্রতিটি পার্সেলের অবস্থা দেখুন, ফেরতসহ।' },
        ],
      },
      {
        label: 'টাকা আদায়',
        steps: [
          { title: 'COD হিসাব', body: 'কোন কুরিয়ারের কাছে কত COD পাওনা, কত দেরি — দেখুন।' },
          { title: 'পেআউট মিলিয়ে দেখুন', body: 'যা আসার কথা আর যা এসেছে — লাইন ধরে মিলিয়ে দেখুন।' },
        ],
      },
      {
        label: 'শিখুন',
        steps: [{ title: 'পারফরম্যান্স বুঝুন', body: 'লাভ, RTO, কুরিয়ার আর পণ্য — আপনার নিজের সংখ্যা থেকে।' }],
      },
    ],
  },
  desktop: {
    eyebrow: 'ওয়েব ওয়ার্কস্পেস',
    title: 'দোকানের টেবিলের ল্যাপটপের জন্য পূর্ণ ওয়ার্কস্পেস',
    lead: 'বেশিরভাগ সিরিয়াস সেলার ল্যাপটপ বা পিসি থেকে ব্যবসা চালান। ecomsbd-এর ব্রাউজার ওয়ার্কস্পেসে পুরো কাজ এক বড় স্ক্রিনে — কিছু ইনস্টল করতে হয় না।',
    items: [
      { title: 'পূর্ণ ড্যাশবোর্ড', body: 'আজকের বিক্রি, COD আর যেখানে নজর দরকার।' },
      { title: 'ঘন অর্ডার টেবিল', body: 'অনেক অর্ডার একসাথে দেখুন, অবস্থা দিয়ে ফিল্টার।' },
      { title: 'বাল্ক কাজ', body: 'অর্ডার বেছে একসাথে বুক করুন।' },
      { title: 'স্টক', body: 'পণ্য, ভ্যারিয়েন্ট, স্টক আর মুভমেন্ট।' },
      { title: 'টাকা ও মিলিয়ে দেখা', body: 'পাওনা, পেআউট আর স্টেটমেন্ট।' },
      { title: 'বিশ্লেষণ', body: 'লাভ, RTO, কুরিয়ার আর পণ্য।' },
      { title: 'ক্রয়', body: 'সাপ্লায়ার, পারচেজ অর্ডার আর পূর্বাভাস।' },
      { title: 'টিম ও সেটিংস', body: 'স্টাফ যুক্ত করুন, কে কী করবেন ঠিক করুন।' },
    ],
    ui: {
      alt: 'ব্রাউজারে ecomsbd অর্ডার টেবিলের নমুনা, কুরিয়ারে বুক করার জন্য তিনটি অর্ডার বাছাই করা।',
      title: 'অর্ডার',
      tabs: ['সব', 'নিশ্চিত', 'কুরিয়ারে পাঠানো', 'ডেলিভারি', 'ফেরত'],
      selected: '৩টি বাছাই করা',
      book: 'কুরিয়ারে বুক করুন',
      hint: 'যেগুলো বুক করা যাবে শুধু সেগুলোই পাঠানো হবে।',
      columns: ['অর্ডার', 'কাস্টমার', 'এলাকা', 'টাকা', 'অবস্থা'],
      rows: [
        ['#1052', 'সাদিয়া ইসলাম', 'ধানমন্ডি, ঢাকা'],
        ['#1051', 'মেহেদী হাসান', 'খুলনা সদর'],
        ['#1050', 'আয়েশা সিদ্দিকা', 'রাজশাহী সদর'],
        ['#1049', 'ইমরান কবির', 'কুমিল্লা সদর'],
        ['#1048', 'নুসরাত জাহান', 'মিরপুর, ঢাকা'],
      ],
    },
  },
  mobile: {
    eyebrow: 'Android অ্যাপ',
    title: 'ডেস্ক ছাড়লেও আপনার ব্যবসা থামে না',
    lead: 'ecomsbd Android অ্যাপ ওয়েব ওয়ার্কস্পেসের সঙ্গী: একই দোকান, একই অর্ডার — আপনার পকেটে।',
    items: [
      'ডেলিভারি, ফেরত, পেআউট আর স্টকের নোটিফিকেশন',
      'কয়েক সেকেন্ডে অর্ডার বা কাস্টমার দেখুন',
      'বাইরে থাকলেও কুরিয়ারের অবস্থা',
      'চলার পথে অর্ডার তৈরি, নিশ্চিত ও বুক',
      'এক নজরে আজকের বিক্রি আর COD',
    ],
    ios: 'iPhone অ্যাপ: শীঘ্রই আসছে।',
    ui: {
      alt: 'ecomsbd Android অ্যাপের নমুনা: আজকের সংখ্যা আর সাম্প্রতিক নোটিফিকেশন।',
      greeting: 'আজ',
      sales: 'মোট বিক্রি',
      cod: 'বকেয়া COD',
      notifications: 'নোটিফিকেশন',
      items: [
        { title: 'পার্সেল ডেলিভারি হয়েছে', meta: '#1046 · Steadfast' },
        { title: 'পেআউটে অমিল পাওয়া গেছে', meta: 'Pathao · {amount}' },
        { title: 'স্টক কম', meta: 'সুতির থ্রি-পিস (M) · {count}টি বাকি' },
        { title: 'পার্সেল ফেরত আসছে', meta: '#1045 · RedX' },
      ],
    },
  },
  together: {
    title: 'এক দোকান। দুই স্ক্রিন। একই সংখ্যা।',
    lead: 'ওয়েব আর Android-এ একই অ্যাকাউন্টে সাইন ইন করুন। দুটোই একই দোকান আর একই ecomsbd ডেটায় কাজ করে — সিঙ্ক করার কিছু নেই, দ্বিতীয় অ্যাকাউন্টও লাগে না।',
    cols: [
      { title: 'ডেস্কটপ', body: 'গভীর কাজ ও পূর্ণ পরিচালনা: বাল্ক বুকিং, মিলিয়ে দেখা, স্টক, রিপোর্ট আর টিম সেটিংস।' },
      { title: 'মোবাইল', body: 'ডেস্ক থেকে দূরে থাকলে সতর্কতা আর দ্রুত কাজ।' },
      { title: 'দুটোই', body: 'এক অ্যাকাউন্ট, এক দোকান, ব্যবসার একই হিসাব।' },
    ],
  },
  couriers: {
    eyebrow: 'কুরিয়ার',
    title: 'আপনার কুরিয়ার অ্যাকাউন্ট যুক্ত করুন',
    lead: 'আপনার যে কুরিয়ার অ্যাকাউন্ট আর রেট আছে সেটাই রাখুন। ecomsbd সেগুলোর সাথে কাজ করে, তাই এক ট্যাব থেকে আরেক ট্যাবে ঠিকানা কপি করতে হয় না।',
    active: 'চালু',
    via: 'আপনার নিজের অ্যাকাউন্টে',
    capabilities: [
      { title: 'অ্যাকাউন্ট যুক্ত করুন', body: 'কুরিয়ারের ক্রেডেনশিয়াল দিন। সেগুলো এনক্রিপ্ট করে রাখা হয়, আর শুধু আংশিক লুকানো অবস্থায় দেখানো হয়।' },
      { title: 'পার্সেল বুক', body: 'একটি অর্ডার বা পুরো ব্যাচ বাল্ক বুক করুন, ঠিকানা আবার না লিখে।' },
      { title: 'অবস্থা অনুসরণ', body: 'অর্ডার থেকেই প্রতিটি পার্সেলের ডেলিভারির অবস্থা দেখুন।' },
      { title: 'COD ওয়ার্কফ্লো', body: 'প্রতিটি কুরিয়ারের প্রত্যাশিত COD, চার্জ আর পেআউট — মিলিয়ে দেখার জন্য তৈরি।' },
      { title: 'কুরিয়ার তুলনা', body: 'ecomsbd-এর সাথে প্রতিটি কুরিয়ার কী করতে পারে বলে যাচাই করা আছে, দেখুন।' },
    ],
    note: 'ডেলিভারির দাম আসে আপনার নিজের কুরিয়ার অ্যাকাউন্ট থেকে। কুরিয়ারের নাম তাদের মালিকদের; ecomsbd তাদের কাছে থাকা আপনার অ্যাকাউন্টের সাথে যুক্ত হয়।',
  },
  money: {
    eyebrow: 'COD ও মিলিয়ে দেখা',
    title: 'পার্সেল ডেলিভারি হলেই বিক্রি শেষ নয়',
    lead: 'ক্যাশ অন ডেলিভারির টাকা আপনার কাছে আসার আগে কুরিয়ারের হাত ঘুরে আসে। পেআউট মিলে যাওয়া পর্যন্ত ecomsbd সেই টাকার হিসাব রাখে।',
    items: [
      { title: 'যত COD আসার কথা', body: 'অর্ডার ও কুরিয়ার ধরে পাওনা, দেরি হওয়াগুলোসহ।' },
      { title: 'আসলে যত এসেছে', body: 'পেআউট লিখে রাখুন, অথবা কুরিয়ারের স্টেটমেন্ট CSV বা Excel হিসেবে আপলোড করুন।' },
      { title: 'পেআউটের পার্থক্য', body: 'প্রত্যাশিত আর পাওয়া টাকা লাইন ধরে তুলনা।' },
      { title: 'কুরিয়ারের কাটা টাকা', body: 'ডেলিভারি ও COD চার্জ আলাদা দেখানো হয়, তাই অজানা কাটা চোখে পড়ে।' },
      { title: 'খোলা অমিল', body: 'সমাধান না করা পর্যন্ত অমিল কেস হিসেবে খোলা থাকে।' },
      { title: 'কন্ট্রিবিউশন লাভ', body: 'পণ্যের খরচ, কুরিয়ার চার্জ আর ফেরতের পরের লাভ।' },
    ],
    note: 'কোনো খরচ বা চার্জ এখনো না জানা থাকলে লাভকে আনুমানিক বলে চিহ্নিত করা হয় — কখনো নিখুঁত বলে দেখানো হয় না।',
    ui: {
      alt: 'ecomsbd-এ কুরিয়ার পেআউট যাচাইয়ের নমুনা: প্রত্যাশিত COD, কুরিয়ার চার্জ, প্রত্যাশিত পেআউট, পাওয়া টাকা আর এক হাজার দুইশ চল্লিশ টাকার পার্থক্য।',
      title: 'পেআউট যাচাই',
      courier: 'Steadfast · {count}টি পার্সেল',
      status: 'অমিল',
      rows: ['আদায় হওয়া COD', 'ডেলিভারি চার্জ', 'COD চার্জ', 'প্রত্যাশিত পেআউট', 'আপনাকে দেওয়া হয়েছে'],
      difference: 'পার্থক্য',
      lines: ['মিলেছে', 'চার্জে অমিল', 'COD পাওয়া যায়নি'],
      openCase: 'খোলা কেস',
    },
  },
  insights: {
    eyebrow: 'বিশ্লেষণ',
    title: 'কী আসলে কাজ করছে, জানুন',
    lead: 'ecomsbd শুধু অর্ডার ট্র্যাকিং নয়। বিশ্লেষণ তৈরি হয় আপনার নিজের অর্ডার, ডেলিভারি, খরচ আর পেআউট থেকে — আর কোনো সংখ্যা আনুমানিক হলে তা স্পষ্ট বলে দেয়।',
    items: [
      'বিক্রি ও অর্ডারের ধারা',
      'লাভের মান: আনুমানিক না চূড়ান্ত',
      'RTO হার ও ফেরতে ক্ষতি',
      'কুরিয়ারের পারফরম্যান্স',
      'পণ্যের পারফরম্যান্স',
      'ক্যাশ ও COD অবস্থা',
      'কাস্টমারের আচরণ',
      'স্টকের অবস্থা',
    ],
    ui: {
      alt: 'গত ৩০ দিনের ecomsbd বিশ্লেষণের নমুনা: ডেলিভারি হওয়া বিক্রি, লাভ, RTO হার, বকেয়া COD, সময়ের সাথে লাভ আর সেরা পণ্য।',
      title: 'বিশ্লেষণ',
      range: '৩০ দিন',
      sales: 'ডেলিভারি হওয়া বিক্রি',
      vsPrev: 'আগের তুলনায় +{change}%',
      profit: 'লাভ',
      estimate: 'আনুমানিক অংশ আছে',
      rto: 'RTO হার',
      rtoOf: '{completed}টির মধ্যে {rto}টি ফেরত',
      receivable: 'বকেয়া COD',
      overdue: '{amount} মেয়াদ পেরিয়েছে',
      trend: 'সময়ের সাথে লাভ',
      perDay: 'প্রতিদিন',
      top: 'সেরা পণ্য',
      units: '{count} ইউনিট',
      products: ['সুতির থ্রি-পিস', 'প্রিন্টেড কুর্তি', 'বাচ্চাদের পাঞ্জাবি'],
      stock: 'স্টক সতর্কতা',
      stockValue: '{low}টি কম · {out}টি শেষ',
    },
  },
  soon: {
    eyebrow: 'শীঘ্রই আসছে',
    badge: 'Coming soon · শীঘ্রই আসছে',
    title: 'সেলস চ্যানেলের সংযোগ আসছে',
    lead: 'আপনি যেখানে বিক্রি করেন সেখান থেকে নিজে থেকে অর্ডার আনার ব্যবস্থা আমরা তৈরি করছি। এই সংযোগগুলো এখনো চালু হয়নি।',
    chip: 'শীঘ্রই আসছে',
    channels: [
      { name: 'Shopify', body: 'স্টোরের অর্ডার নিজে থেকেই আসবে।' },
      { name: 'WooCommerce', body: 'WordPress দোকানের জন্য।' },
      { name: 'নিজের ওয়েবসাইট', body: 'আপনার ওয়েবসাইটের অর্ডার।' },
      { name: 'Messenger', body: 'চ্যাটের অর্ডার খসড়া হিসেবে, দেখে নেওয়ার জন্য।' },
      { name: 'WhatsApp', body: 'WhatsApp Business চ্যাটের অর্ডার।' },
    ],
    today: 'এখন কাস্টমারের যেকোনো মেসেজ ecomsbd-এ পেস্ট করুন — অর্ডারের তথ্য সে নিজেই বের করবে।',
  },
  trust: {
    eyebrow: 'নিরাপত্তা ও আস্থা',
    title: 'ব্যবসার আসল তথ্যের জন্য তৈরি',
    lead: 'আপনার অর্ডার, কাস্টমার আর টাকার হিসাব আপনার ব্যবসার। ecomsbd প্রতিটি দোকানের তথ্য আলাদা রাখে, আর কে কী দেখবে তা আপনি ঠিক করেন।',
    items: [
      { title: 'রোল অনুযায়ী টিমের অ্যাক্সেস', body: 'স্টাফ যুক্ত করুন, প্রতিটি রোল কী দেখবে ও করবে ঠিক করুন — টাকার সংখ্যাসহ।' },
      { title: 'দোকান-ভিত্তিক আলাদা তথ্য', body: 'প্রতিটি দোকানের অর্ডার, কাস্টমার আর টাকার হিসাব অন্য সব দোকান থেকে আলাদা।' },
      { title: 'সংবেদনশীল কাজের রেকর্ড', body: 'গুরুত্বপূর্ণ পরিবর্তন কে করেছেন আর কখন — রেকর্ড থাকে।' },
      { title: 'নিরাপদ ওয়ার্কস্পেস', body: 'সব জায়গায় HTTPS, আর ইমেইল, Google বা Apple দিয়ে সাইন-ইন করা অ্যাক্সেস।' },
      { title: 'এনক্রিপ্ট করা ক্রেডেনশিয়াল', body: 'কুরিয়ার ও যুক্ত সার্ভিসের ক্রেডেনশিয়াল এনক্রিপ্ট করা, আর শুধু আংশিক লুকানো অবস্থায় দেখানো হয়।' },
    ],
    link: 'গোপনীয়তা নীতি পড়ুন',
  },
  free: {
    eyebrow: 'লঞ্চের সময়',
    title: 'লঞ্চের সময় বিনামূল্যে ecomsbd ব্যবহার শুরু করুন',
    lead: 'লঞ্চের এই সময়ে ecomsbd-এ যা চালু আছে সবই বিনামূল্যে। আপনার দোকান তৈরি করে আজই শুরু করুন — কোনো চেকআউট নেই, কার্ডও দিতে হয় না।',
    points: [
      'ওয়েব ওয়ার্কস্পেস আর Android অ্যাপ',
      'Steadfast, Pathao আর RedX সংযোগ',
      'একই দোকানে আপনার টিম',
      'কার্ড বা পেমেন্টের তথ্য লাগে না',
    ],
  },
  faq: {
    eyebrow: 'প্রশ্নোত্তর',
    title: 'সেলাররা যা জানতে চান',
    support: 'আরও প্রশ্ন? সাপোর্টে যোগাযোগ করুন',
    items: [
      {
        q: 'ecomsbd কী?',
        a: 'ecomsbd বাংলাদেশের অনলাইন ও এফ-কমার্স সেলারদের ব্যবসা পরিচালনার প্ল্যাটফর্ম। অর্ডার, কাস্টমার, কুরিয়ার বুকিং, COD-এর টাকা, স্টক আর লাভ এক জায়গায় থাকে — তাই স্প্রেডশিট, কুরিয়ার ড্যাশবোর্ড আর খাতা নিয়ে ছোটাছুটি না করেই দোকান চালাতে পারেন।',
      },
      {
        q: 'এটা কাদের জন্য?',
        a: 'যাঁরা ফেসবুক পেজ, চ্যাট, ফোন বা নিজের চ্যানেলে অর্ডার নেন আর ক্যাশ অন ডেলিভারিতে কুরিয়ারে পাঠান — একা সেলার থেকে ছোট টিম পর্যন্ত।',
      },
      {
        q: 'কম্পিউটার থেকে ব্যবহার করা যাবে?',
        a: 'হ্যাঁ। পুরো ওয়ার্কস্পেস চলে আপনার ব্রাউজারে, app.ecomsbd.com-এ — যেকোনো ল্যাপটপ বা পিসিতে। কিছু ইনস্টল করতে হয় না।',
      },
      {
        q: 'Android অ্যাপ আছে?',
        a: 'হ্যাঁ। ecomsbd Android অ্যাপ Google Play-তে আছে। নোটিফিকেশন, দ্রুত দেখে নেওয়া আর চলার পথে অর্ডারের জন্য এটা ওয়েব ওয়ার্কস্পেসের সঙ্গী। iPhone অ্যাপ শীঘ্রই আসছে।',
      },
      {
        q: 'কোন কোন কুরিয়ার সাপোর্ট করে?',
        a: 'Steadfast, Pathao আর RedX। আপনার নিজের কুরিয়ার অ্যাকাউন্ট যুক্ত করুন, তারপর ecomsbd থেকেই পার্সেল বুক, ডেলিভারির অবস্থা দেখা আর COD-এর হিসাব রাখুন।',
      },
      {
        q: 'আমার টিম কি একই দোকান ব্যবহার করতে পারবে?',
        a: 'হ্যাঁ। টিম মেম্বারদের আপনার দোকানে যুক্ত করে প্রত্যেককে একটি রোল দিন। রোল ঠিক করে তাঁরা কী দেখবেন ও করবেন — টাকার সংখ্যা দেখবেন কি না তাও।',
      },
      {
        q: 'ecomsbd কি আমার কুরিয়ার অ্যাকাউন্টের বদলে কাজ করে?',
        a: 'না। আপনার কুরিয়ার অ্যাকাউন্ট, রেট আর পেআউট আপনারই থাকে। পার্সেল বুক ও অনুসরণ আর পেআউট যাচাইয়ের জন্য ecomsbd সেগুলোর সাথে যুক্ত হয়। ক্যাশ অন ডেলিভারির টাকা কুরিয়ারই তোলে ও দেয়; ecomsbd তা নেয় না।',
      },
      {
        q: 'এখন কি বিনামূল্যে?',
        a: 'হ্যাঁ। লঞ্চের সময় ecomsbd বিনামূল্যে, আর বিলিং বন্ধ আছে — তাই কোনো চেকআউট নেই, কার্ড বা পেমেন্টের তথ্যও চাওয়া হয় না।',
      },
      {
        q: 'Messenger, WhatsApp, Shopify বা WooCommerce সংযোগ কি চালু আছে?',
        a: 'এখনো না। Shopify, WooCommerce, নিজের ওয়েবসাইট, Messenger আর WhatsApp সংযোগ শীঘ্রই আসছে। এখন কাস্টমারের মেসেজ পেস্ট করলে ecomsbd অর্ডারের তথ্য বের করে দেয়।',
      },
      {
        q: 'আমার ব্যবসার তথ্য কোথায় রাখা হয়?',
        a: 'আপনার ecomsbd দোকানে, ecomsbd-এর ক্লাউড সার্ভিসে — অন্য সব দোকান থেকে আলাদা করে। আমাদের কিছু ক্লাউড প্রোভাইডার বাংলাদেশের বাইরে কাজ করে, তাই তথ্য সেখানেও রাখা হতে পারে। কোন প্রোভাইডার ব্যবহার হয় আর কীভাবে অ্যাকাউন্ট মুছবেন, গোপনীয়তা নীতিতে লেখা আছে।',
      },
    ],
  },
  final: {
    title: 'অনুমান কমিয়ে আপনার ব্যবসা চালান।',
    lead: 'অর্ডার, কুরিয়ার, COD আর লাভ — প্রথম মেসেজ থেকে শেষ পেআউট পর্যন্ত জোড়া।',
  },
  footer: {
    tagline: 'বাংলাদেশের সেলারদের ব্যবসা পরিচালনা',
    product: 'প্রোডাক্ট',
    resources: 'সহায়তা',
    legal: 'আইনি',
    account: 'অ্যাকাউন্ট',
    webApp: 'ওয়েব অ্যাপ',
    android: 'Android অ্যাপ',
    privacy: 'গোপনীয়তা নীতি',
    deletion: 'অ্যাকাউন্ট মুছে ফেলা',
    rights: 'সর্বস্বত্ব সংরক্ষিত।',
  },
};

export const copy: Record<Locale, SiteCopy> = { en, bn };

/** Fills `{name}` placeholders, the same shape the workspace strings use. */
export function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) => values[key] ?? match);
}
