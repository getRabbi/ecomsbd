/// Copy added by the final-prototype visual pass: the Home summary, daily
/// plan, courier saving preview and channel strip, the compact order card's
/// actions, and the prototype's screen headers.
library;

const finalUiEn = <String, String>{
  // Home
  'home.quickSub': 'Your most-used work',
  'home.stat.orders': 'Orders',
  'home.stat.delivered': 'Delivered',
  'home.stat.codDue': 'COD due',
  'home.stat.profit': 'Profit',
  'home.todayTitle': 'Today',
  'home.todaySub': 'How today’s parcels finished',
  'home.progressTitle': '{delivered} delivered · {returned} returned',
  'home.progressNone': 'No parcels finished today yet',
  'home.progressMoney': '{realized} realized today · {cod} with couriers',
  'home.channelsTitle': 'Sales channels',
  'home.channelsSub': 'Where orders and messages come from',
  'home.pulseSub': 'Last 30 days · full charts in Insights',
  'home.pulseParcels': 'Settled parcels',
  'home.pulseDelivered': 'Delivered of dispatched',
  'sug.planTitle': 'Your daily plan',
  'sug.planSub': 'Do the highest-impact work first.',
  'sug.badge': 'From shop data',
  'sug.checking': 'Checking today’s orders…',
  'sug.emptyTitle': 'You’re on top of today',
  'sug.emptyBody':
      'Suggestions appear when your orders, couriers, messages or money '
      'need a decision.',
  'sug.footer': 'Each suggestion says which data raised it — no generic tips.',
  'save.title': 'Courier saving opportunity',
  'save.subtitle': 'Before you book today’s parcels',
  'save.full': 'Full compare',
  'save.unbooked.one': '1 unbooked parcel',
  'save.unbooked.other': '{count} unbooked parcels',
  'save.oneParcel': 'One standard parcel',
  'save.sample': 'Sample rates',
  'save.assumption': 'Sample estimate · Inside Dhaka · 1 kg',
  'save.quoted': 'Your couriers’ quotes',
  'save.possible': 'possible saving',
  'save.courier': 'Courier',
  'save.parcels.one': '1 parcel',
  'save.parcels.other': '{count} parcels',
  'save.difference': 'Difference',
  'save.lowest': 'Lowest',

  // Orders
  'ord.compareBook': 'Compare & book courier',
  'ord.reviewRisk': 'Review risk',
  'ord.open': 'Open order',

  // Money
  'money.courierCosts': 'Courier costs',
  'money.courierCostsSub': 'Compare real delivery cost',

  // More
  'more.eyebrow': 'Everything else, grouped clearly',
  'more.description': 'Find any tool without remembering where it lives.',
  'more.connectionHealth': 'Connection health',
  'more.connectionHealthSub': 'Check what is syncing automatically',

  // Courier compare and connections
  'cmp.eyebrow': 'Delivery cost intelligence',
  'cmp.controls': 'This parcel',
  'conn.eyebrow': 'Connections',
};

const finalUiBn = <String, String>{
  // Home
  'home.quickSub': 'সবচেয়ে বেশি যা করেন',
  'home.stat.orders': 'অর্ডার',
  'home.stat.delivered': 'ডেলিভারি',
  'home.stat.codDue': 'বাকি COD',
  'home.stat.profit': 'লাভ',
  'home.todayTitle': 'আজ',
  'home.todaySub': 'আজকের পার্সেলগুলো কেমন গেল',
  'home.progressTitle': '{delivered}টি ডেলিভারি · {returned}টি ফেরত',
  'home.progressNone': 'আজ এখনো কোনো পার্সেল শেষ হয়নি',
  'home.progressMoney': 'আজ হাতে এসেছে {realized} · কুরিয়ারের কাছে {cod}',
  'home.channelsTitle': 'বিক্রির চ্যানেল',
  'home.channelsSub': 'অর্ডার আর মেসেজ যেখান থেকে আসে',
  'home.pulseSub': 'শেষ ৩০ দিন · পুরো চার্ট ইনসাইটসে',
  'home.pulseParcels': 'সেটেল হওয়া পার্সেল',
  'home.pulseDelivered': 'পাঠানো থেকে ডেলিভারি',
  'sug.planTitle': 'আজকের পরিকল্পনা',
  'sug.planSub': 'সবচেয়ে জরুরি কাজটা আগে করুন।',
  'sug.badge': 'দোকানের তথ্য থেকে',
  'sug.checking': 'আজকের অর্ডার দেখা হচ্ছে…',
  'sug.emptyTitle': 'আজকের কাজ নিয়ন্ত্রণে আছে',
  'sug.emptyBody':
      'অর্ডার, কুরিয়ার, মেসেজ বা টাকায় সিদ্ধান্ত লাগলে এখানে পরামর্শ আসবে।',
  'sug.footer':
      'প্রতিটি পরামর্শ কোন তথ্য থেকে এসেছে তা বলে দেয় — সাধারণ টিপস নয়।',
  'save.title': 'কুরিয়ারে সাশ্রয়ের সুযোগ',
  'save.subtitle': 'আজকের পার্সেল বুক করার আগে',
  'save.full': 'পুরো তুলনা',
  'save.unbooked.one': '১টি পার্সেল বুক বাকি',
  'save.unbooked.other': '{count}টি পার্সেল বুক বাকি',
  'save.oneParcel': 'একটি সাধারণ পার্সেল',
  'save.sample': 'নমুনা রেট',
  'save.assumption': 'নমুনা হিসাব · ঢাকার ভিতরে · ১ কেজি',
  'save.quoted': 'আপনার কুরিয়ারের কোট',
  'save.possible': 'সম্ভাব্য সাশ্রয়',
  'save.courier': 'কুরিয়ার',
  'save.parcels.one': '১টি পার্সেল',
  'save.parcels.other': '{count}টি পার্সেল',
  'save.difference': 'পার্থক্য',
  'save.lowest': 'সবচেয়ে কম',

  // Orders
  'ord.compareBook': 'তুলনা করে কুরিয়ার বুক',
  'ord.reviewRisk': 'ঝুঁকি দেখুন',
  'ord.open': 'অর্ডার খুলুন',

  // Money
  'money.courierCosts': 'কুরিয়ার খরচ',
  'money.courierCostsSub': 'আসল ডেলিভারি খরচ তুলনা',

  // More
  'more.eyebrow': 'বাকি সব টুল, গুছিয়ে রাখা',
  'more.description': 'কোন টুল কোথায় আছে মনে না রেখেই খুঁজে নিন।',
  'more.connectionHealth': 'কানেকশনের অবস্থা',
  'more.connectionHealthSub': 'কী কী অটো সিঙ্ক হচ্ছে দেখুন',

  // Courier compare and connections
  'cmp.eyebrow': 'ডেলিভারি খরচের হিসাব',
  'cmp.controls': 'এই পার্সেল',
  'conn.eyebrow': 'কানেকশন',
};
