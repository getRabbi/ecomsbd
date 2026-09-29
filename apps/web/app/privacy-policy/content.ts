// Paired translations keep the public, server-rendered policies in sync.
export type PolicyLocale = 'en' | 'bn';
export const updated = '2026-09-29';
export const policySections = [
  {
    id: 'scope',
    en: { title: '1. About this policy', paragraphs: [
      'ecomsbd is a Bangladesh-first seller operations and commerce management platform. This Privacy Policy explains how we collect, use, store and share information through the ecomsbd Android app (com.ecomsbd.app) and the web platform at scalemyprints.com. “We”, “us” and “our” refer to the operation of ecomsbd.',
      'This policy applies to sellers, shop owners, their team members and visitors to our website. It also describes how we process information about customers and suppliers that sellers enter, import or sync into the service. Sellers are responsible for their own customer notices, permissions and lawful use of that information.'
    ] },
    bn: { title: '১. এই নীতি সম্পর্কে', paragraphs: [
      'ecomsbd বাংলাদেশের বিক্রেতাদের জন্য তৈরি বিক্রয় কার্যক্রম ও বাণিজ্য ব্যবস্থাপনা প্ল্যাটফর্ম। ecomsbd Android অ্যাপ (com.ecomsbd.app) এবং scalemyprints.com ওয়েব প্ল্যাটফর্মের মাধ্যমে আমরা কীভাবে তথ্য সংগ্রহ, ব্যবহার, সংরক্ষণ ও শেয়ার করি, তা এই গোপনীয়তা নীতিতে বলা হয়েছে। “আমরা” ও “আমাদের” বলতে ecomsbd-এর পরিচালনাকে বোঝানো হয়েছে।',
      'এই নীতি বিক্রেতা, দোকানের মালিক, তাঁদের দলের সদস্য এবং আমাদের ওয়েবসাইটের দর্শকদের জন্য প্রযোজ্য। বিক্রেতারা সেবায় যে ক্রেতা ও সরবরাহকারীর তথ্য লেখেন, আমদানি করেন বা সিঙ্ক করেন, তা কীভাবে প্রক্রিয়া করা হয়, সেটিও এতে বলা হয়েছে। ক্রেতাদের প্রয়োজনীয় নোটিশ দেওয়া, অনুমতি নেওয়া এবং সেই তথ্য আইনসম্মতভাবে ব্যবহার করার দায়িত্ব বিক্রেতার।'
    ] }
  },
  {
    id: 'account',
    en: { title: '2. Account, shop and team information', paragraphs: [
      'When you register or sign in, Supabase Auth processes your email address, password for email/password sign-in, authentication identifiers and session information. If you choose Google Sign-In, Google and Supabase process that sign-in and provide account identifiers and available profile information, such as your email and name. Your Google password is not provided to ecomsbd.',
      'We store account identifiers, linked sign-in information, display name, language preference and account status. We also process contact details you provide, shop name and business category, pickup contact and address, shop settings, team invitations, membership and roles. What a team member can view or change depends on their shop permissions.'
    ] },
    bn: { title: '২. অ্যাকাউন্ট, দোকান ও দলের তথ্য', paragraphs: [
      'নিবন্ধন বা সাইন-ইনের সময় Supabase Auth আপনার ইমেইল ঠিকানা, ইমেইল/পাসওয়ার্ড সাইন-ইনের পাসওয়ার্ড, পরিচয় যাচাইয়ের শনাক্তকারী এবং সেশনের তথ্য প্রক্রিয়া করে। Google Sign-In বেছে নিলে Google ও Supabase সেই সাইন-ইন প্রক্রিয়া করে এবং অ্যাকাউন্টের শনাক্তকারী ও উপলব্ধ প্রোফাইল তথ্য, যেমন ইমেইল ও নাম, সরবরাহ করে। আপনার Google পাসওয়ার্ড ecomsbd-কে দেওয়া হয় না।',
      'আমরা অ্যাকাউন্টের শনাক্তকারী, সংযুক্ত সাইন-ইনের তথ্য, প্রদর্শিত নাম, ভাষার পছন্দ ও অ্যাকাউন্টের অবস্থা সংরক্ষণ করি। আপনার দেওয়া যোগাযোগের তথ্য, দোকানের নাম ও ব্যবসার ধরন, পিকআপের যোগাযোগ ও ঠিকানা, দোকানের সেটিংস, দলের আমন্ত্রণ, সদস্যপদ ও ভূমিকাও প্রক্রিয়া করি। দলের সদস্য কী দেখতে বা পরিবর্তন করতে পারবেন, তা দোকানে তাঁর অনুমতির ওপর নির্ভর করে।'
    ] }
  },
  {
    id: 'commerce',
    en: { title: '3. Customer, order and business information', paragraphs: [
      'Sellers may provide customer names, phone and alternate phone numbers, delivery addresses, district/area, order details, notes, tags, follow-ups and shop-specific customer flags. Customer email addresses or WhatsApp contact details may be recorded for messaging. Orders can include the original text pasted by the seller, address snapshots, purchased items, prices, discounts, delivery fees, status, delivery outcomes, returns and return-to-origin (RTO) history.',
      'We process product and variant details, SKUs, prices, costs, inventory and stock movements; supplier names, contact names, phone numbers, email addresses, addresses and notes; purchase orders, receipts, supplier payments and payables. Uploaded spreadsheets, import rows, payout statements and generated export files may contain these same categories of information.',
      'Customer/order information is processed on behalf of the seller to operate their shop, fulfil orders, arrange delivery, reconcile money, manage customer relationships and provide the features they use. Raw identifiable customer records and a shop’s private notes are not made available to unrelated sellers. Customers should normally contact the seller they bought from to ask about their order or request access, correction or deletion of information held for that shop.'
    ] },
    bn: { title: '৩. ক্রেতা, অর্ডার ও ব্যবসার তথ্য', paragraphs: [
      'বিক্রেতারা ক্রেতার নাম, ফোন ও বিকল্প ফোন নম্বর, ডেলিভারির ঠিকানা, জেলা/এলাকা, অর্ডারের বিবরণ, নোট, ট্যাগ, ফলো-আপ এবং দোকানের নিজস্ব ক্রেতা-সংক্রান্ত চিহ্নিতকরণ দিতে পারেন। বার্তা পাঠানোর জন্য ক্রেতার ইমেইল বা WhatsApp যোগাযোগের তথ্য রাখা হতে পারে। অর্ডারে বিক্রেতার পেস্ট করা মূল লেখা, সেই সময়ের ঠিকানা, কেনা পণ্য, দাম, ছাড়, ডেলিভারি ফি, অবস্থা, ডেলিভারির ফল, ফেরত ও প্রেরকের কাছে ফেরত (RTO) যাওয়ার ইতিহাস থাকতে পারে।',
      'আমরা পণ্য ও ভ্যারিয়েন্টের বিবরণ, SKU, দাম, খরচ, মজুত ও মজুতের পরিবর্তন; সরবরাহকারীর নাম, যোগাযোগকারী ব্যক্তির নাম, ফোন, ইমেইল, ঠিকানা ও নোট; ক্রয়াদেশ, পণ্য গ্রহণ, সরবরাহকারীকে পরিশোধ ও দেনার তথ্য প্রক্রিয়া করি। আপলোড করা স্প্রেডশিট, আমদানির সারি, পেআউট বিবরণী ও তৈরি করা এক্সপোর্ট ফাইলেও একই ধরনের তথ্য থাকতে পারে।',
      'দোকান পরিচালনা, অর্ডার পূরণ, ডেলিভারির ব্যবস্থা, অর্থের হিসাব মেলানো, ক্রেতার সঙ্গে সম্পর্ক ব্যবস্থাপনা এবং বিক্রেতার ব্যবহৃত সুবিধাগুলো দেওয়ার জন্য তাঁর পক্ষ থেকে ক্রেতা/অর্ডারের তথ্য প্রক্রিয়া করা হয়। শনাক্তযোগ্য ক্রেতার মূল রেকর্ড বা দোকানের ব্যক্তিগত নোট সম্পর্কহীন অন্য বিক্রেতাকে দেখানো হয় না। অর্ডার সম্পর্কে জানতে বা দোকানের কাছে থাকা তথ্য দেখা, সংশোধন বা মুছে ফেলার অনুরোধ করতে ক্রেতার সাধারণত যে বিক্রেতার কাছ থেকে কেনাকাটা করেছেন, তাঁর সঙ্গে যোগাযোগ করা উচিত।'
    ] }
  },
  {
    id: 'financial',
    en: { title: '4. Financial and operational information', paragraphs: [
      'We process cash-on-delivery (COD) amounts, receivables, courier charges and payouts, reconciliation results and discrepancies, transaction references, ledger entries, cashflow, expenses, costs and profit calculations. These are records used to manage the seller’s business; ecomsbd does not itself collect the customer’s COD payment.',
      'ecomsbd currently operates in free launch mode with billing disabled. We do not currently collect card, bKash or Nagad payment credentials for an ecomsbd subscription. Business payment amounts or references entered by a seller are distinct from payment credentials.'
    ] },
    bn: { title: '৪. আর্থিক ও পরিচালনাগত তথ্য', paragraphs: [
      'আমরা ক্যাশ অন ডেলিভারি (COD)-এর পরিমাণ, পাওনা, কুরিয়ার চার্জ ও পেআউট, হিসাব মেলানোর ফল ও অমিল, লেনদেনের রেফারেন্স, লেজার এন্ট্রি, নগদ প্রবাহ, ব্যয়, খরচ ও লাভের হিসাব প্রক্রিয়া করি। এগুলো বিক্রেতার ব্যবসা পরিচালনার রেকর্ড; ecomsbd নিজে ক্রেতার COD অর্থ সংগ্রহ করে না।',
      'ecomsbd বর্তমানে ফ্রি লঞ্চ মোডে চলছে এবং বিলিং বন্ধ আছে। ecomsbd সাবস্ক্রিপশনের জন্য আমরা বর্তমানে কার্ড, bKash বা Nagad-এর পেমেন্ট ক্রেডেনশিয়াল সংগ্রহ করি না। বিক্রেতার লেখা ব্যবসায়িক পরিশোধের পরিমাণ বা রেফারেন্স পেমেন্ট ক্রেডেনশিয়াল থেকে আলাদা।'
    ] }
  },
  {
    id: 'technical',
    en: { title: '5. Device information, local storage and diagnostics', paragraphs: [
      'We process app installation identifiers, device platform, device model and operating system/app versions where reported, session identifiers and activity timestamps. Firebase Cloud Messaging uses registration tokens and installation/device information to deliver push notifications; we associate registered push tokens with signed-in devices.',
      'Our systems process IP addresses and request metadata to handle connections and protect the service. Application diagnostics include request paths, methods, status codes, timing, request/trace identifiers, and relevant user or shop identifiers. Security/session records can include user-agent information and hashed IP information. Hosting and authentication providers also process connection metadata to operate their services.',
      'The web platform uses authentication cookies and browser storage for sessions and language preferences. The mobile app stores session credentials securely and keeps local business data and pending changes to support offline work and later synchronisation. Files you deliberately choose to upload are processed for that feature; the app does not request access to your contacts, SMS inbox, call logs, microphone, camera or precise device location.',
      'Our analytics features calculate the seller’s operational figures from business records. We do not currently run third-party product/usage analytics, advertising trackers or session replay in the web or mobile app. Firebase Analytics and Crashlytics are not integrated. Sentry error-reporting support exists on the backend but is not enabled in the current production configuration.'
    ] },
    bn: { title: '৫. ডিভাইসের তথ্য, স্থানীয় সংরক্ষণ ও ত্রুটি নির্ণয়', paragraphs: [
      'আমরা অ্যাপ ইনস্টলেশনের শনাক্তকারী, ডিভাইসের প্ল্যাটফর্ম, পাঠানো হলে ডিভাইসের মডেল ও অপারেটিং সিস্টেম/অ্যাপের সংস্করণ, সেশনের শনাক্তকারী এবং কার্যক্রমের সময় প্রক্রিয়া করি। পুশ নোটিফিকেশন পৌঁছাতে Firebase Cloud Messaging রেজিস্ট্রেশন টোকেন ও ইনস্টলেশন/ডিভাইসের তথ্য ব্যবহার করে; আমরা নিবন্ধিত পুশ টোকেনকে সাইন-ইন করা ডিভাইসের সঙ্গে যুক্ত করি।',
      'সংযোগ পরিচালনা ও সেবা সুরক্ষিত রাখতে আমাদের সিস্টেম IP ঠিকানা ও অনুরোধের মেটাডেটা প্রক্রিয়া করে। অ্যাপ্লিকেশনের ত্রুটি নির্ণয়ের তথ্যে অনুরোধের পথ, পদ্ধতি, স্ট্যাটাস কোড, সময়কাল, অনুরোধ/ট্রেসের শনাক্তকারী এবং প্রাসঙ্গিক ব্যবহারকারী বা দোকানের শনাক্তকারী থাকে। নিরাপত্তা/সেশনের রেকর্ডে ইউজার-এজেন্ট ও হ্যাশ করা IP তথ্য থাকতে পারে। হোস্টিং ও পরিচয় যাচাই সেবাদাতারাও তাঁদের সেবা চালাতে সংযোগের মেটাডেটা প্রক্রিয়া করেন।',
      'ওয়েব প্ল্যাটফর্ম সেশন ও ভাষার পছন্দের জন্য পরিচয় যাচাইয়ের কুকি এবং ব্রাউজার স্টোরেজ ব্যবহার করে। মোবাইল অ্যাপ সেশনের ক্রেডেনশিয়াল সুরক্ষিতভাবে রাখে এবং অফলাইনে কাজ ও পরে সিঙ্ক করার জন্য ডিভাইসে ব্যবসার তথ্য ও অপেক্ষমাণ পরিবর্তন রাখে। আপনি যে ফাইল ইচ্ছাকৃতভাবে আপলোডের জন্য বেছে নেন, তা সংশ্লিষ্ট সুবিধার জন্য প্রক্রিয়া করা হয়; অ্যাপ আপনার কন্টাক্ট, SMS ইনবক্স, কল লগ, মাইক্রোফোন, ক্যামেরা বা ডিভাইসের সুনির্দিষ্ট অবস্থানে প্রবেশের অনুমতি চায় না।',
      'আমাদের বিশ্লেষণ সুবিধা ব্যবসার রেকর্ড থেকে বিক্রেতার পরিচালনাগত হিসাব তৈরি করে। ওয়েব বা মোবাইল অ্যাপে বর্তমানে তৃতীয় পক্ষের পণ্য/ব্যবহার বিশ্লেষণ, বিজ্ঞাপনী ট্র্যাকার বা সেশন রিপ্লে চালানো হয় না। Firebase Analytics ও Crashlytics সংযুক্ত নেই। ব্যাকএন্ডে Sentry দিয়ে ত্রুটি জানানোর ব্যবস্থা আছে, তবে বর্তমান প্রোডাকশন কনফিগারেশনে তা চালু নেই।'
    ] }
  },
  {
    id: 'integrations',
    en: { title: '6. Connected services and messaging', paragraphs: [
      'When you connect an available courier integration, such as Steadfast, Pathao or RedX, we process the merchant account details and credentials you provide. Booking or managing a parcel can share recipient name, phone, delivery/pickup address, items, delivery instructions, COD amount and parcel references with that courier, and return delivery, return and settlement information to your shop. Manual courier records do not by themselves connect to a courier API.',
      'When an available Shopify, WooCommerce or Custom Website connection is enabled, we process store identifiers, access credentials, selected orders/customer information, products, variants, stock, fulfilment/status information and webhook/synchronisation records as required by the selected features. Seller-created API keys and webhooks can allow the seller’s authorised software and designated endpoints to receive the data permitted by their scopes and subscribed events.',
      'Messaging features store the contacts you enter, message templates, rendered message content, campaign selections, consent/opt-out evidence, sending attempts and provider references. Delivery or read/open statuses are processed only where the configured channel reports them. Sending through WhatsApp requires an enabled Meta/WhatsApp Business connection and approved templates. The Messenger connection implementation handles Page connection and webhook health; it does not store or answer Messenger message contents, and Messenger campaign sending is unavailable.',
      'An adapter being present does not mean a provider receives information. Sharing occurs only when a service is active and the relevant connection or feature is used. In the current deployment, Shopify and Meta setup credentials are absent, business email delivery is disabled, and customer SMS sending is unavailable. The optional Resend email transport would receive recipient addresses and message content only if configured and used. These gated services are not current recipients merely because their integrations exist.'
    ] },
    bn: { title: '৬. সংযুক্ত সেবা ও বার্তা', paragraphs: [
      'Steadfast, Pathao বা RedX-এর মতো উপলব্ধ কুরিয়ার সংযোগ চালু করলে আমরা আপনার দেওয়া মার্চেন্ট অ্যাকাউন্টের তথ্য ও ক্রেডেনশিয়াল প্রক্রিয়া করি। পার্সেল বুকিং বা পরিচালনার সময় প্রাপকের নাম, ফোন, ডেলিভারি/পিকআপের ঠিকানা, পণ্য, ডেলিভারির নির্দেশনা, COD-এর পরিমাণ ও পার্সেলের রেফারেন্স কুরিয়ারের সঙ্গে শেয়ার হতে পারে এবং ডেলিভারি, ফেরত ও নিষ্পত্তির তথ্য দোকানে আসতে পারে। শুধু ম্যানুয়াল কুরিয়ার রেকর্ড তৈরি করলে কুরিয়ার API-তে সংযোগ হয় না।',
      'উপলব্ধ Shopify, WooCommerce বা Custom Website সংযোগ চালু করলে নির্বাচিত সুবিধার প্রয়োজন অনুযায়ী আমরা স্টোরের শনাক্তকারী, অ্যাক্সেস ক্রেডেনশিয়াল, নির্বাচিত অর্ডার/ক্রেতার তথ্য, পণ্য, ভ্যারিয়েন্ট, মজুত, অর্ডার পূরণ/অবস্থা এবং ওয়েবহুক/সিঙ্কের রেকর্ড প্রক্রিয়া করি। বিক্রেতার তৈরি API key ও webhook তাঁর অনুমোদিত সফটওয়্যার এবং নির্ধারিত এন্ডপয়েন্টকে অনুমতির পরিধি ও নির্বাচিত ইভেন্ট অনুযায়ী তথ্য পেতে দিতে পারে।',
      'বার্তার সুবিধায় আপনার দেওয়া যোগাযোগের তথ্য, বার্তার টেমপ্লেট, তৈরি হওয়া বার্তার বিষয়বস্তু, ক্যাম্পেইনের নির্বাচন, সম্মতি/অনাগ্রহের প্রমাণ, পাঠানোর চেষ্টা ও সেবাদাতার রেফারেন্স রাখা হয়। কনফিগার করা চ্যানেল জানালেই কেবল পৌঁছানো বা পড়া/খোলার অবস্থা প্রক্রিয়া করা হয়। WhatsApp-এ পাঠাতে চালু Meta/WhatsApp Business সংযোগ এবং অনুমোদিত টেমপ্লেট লাগে। Messenger সংযোগের বাস্তবায়ন Page সংযোগ ও webhook-এর কার্যকারিতা পরিচালনা করে; এটি Messenger বার্তার বিষয়বস্তু সংরক্ষণ বা উত্তর দেয় না এবং Messenger ক্যাম্পেইন পাঠানো উপলব্ধ নয়।',
      'কোনো অ্যাডাপ্টার থাকলেই সেই সেবাদাতা তথ্য পায় না। সেবা সক্রিয় থাকলে এবং সংশ্লিষ্ট সংযোগ বা সুবিধা ব্যবহার করা হলেই তথ্য শেয়ার হয়। বর্তমান ডিপ্লয়মেন্টে Shopify ও Meta সেটআপের ক্রেডেনশিয়াল নেই, ব্যবসায়িক ইমেইল পাঠানো বন্ধ এবং ক্রেতাকে SMS পাঠানো উপলব্ধ নয়। ঐচ্ছিক Resend ইমেইল ব্যবস্থা কনফিগার করে ব্যবহার করা হলেই প্রাপকের ঠিকানা ও বার্তার বিষয়বস্তু পাবে। শুধু ইন্টিগ্রেশন থাকার কারণে এই শর্তসাপেক্ষ সেবাগুলো বর্তমানে তথ্যপ্রাপক নয়।'
    ] }
  },
  {
    id: 'uses',
    en: { title: '7. How we use information', paragraphs: [
      'We use information to authenticate accounts, manage shops and team access, process and synchronise orders, maintain inventory, arrange seller-requested courier operations, reconcile COD and payouts, calculate costs/profit, manage procurement and customer relationships, deliver configured messages and notifications, run seller-selected workflows, provide exports, investigate errors and abuse, maintain security and respond to requests.',
      'First-party risk/history features use the relevant shop’s own customer and delivery history. No external risk provider is currently registered, so the external-risk feature does not currently send customer lookups to a third party. It is not a shared identifiable customer blacklist.',
      'If a shop opts in to network intelligence, its eligible operational results can contribute to aggregate benchmarks. Published benchmarks use minimum participation/sample requirements, contribution limits and coarse rounding; insufficient groups are suppressed. They do not reveal another seller’s identity, individual customer records or exact shop-level results. You can change participation in the Network intelligence settings; previously published anonymous aggregates are not individual customer records.'
    ] },
    bn: { title: '৭. আমরা তথ্য যেভাবে ব্যবহার করি', paragraphs: [
      'অ্যাকাউন্ট যাচাই, দোকান ও দলের অ্যাক্সেস পরিচালনা, অর্ডার প্রক্রিয়া ও সিঙ্ক, মজুত রাখা, বিক্রেতার অনুরোধে কুরিয়ারের কাজ, COD ও পেআউটের হিসাব মেলানো, খরচ/লাভ হিসাব, ক্রয় ও ক্রেতার সম্পর্ক ব্যবস্থাপনা, কনফিগার করা বার্তা ও নোটিফিকেশন পাঠানো, বিক্রেতার নির্বাচিত ওয়ার্কফ্লো চালানো, এক্সপোর্ট দেওয়া, ত্রুটি ও অপব্যবহার তদন্ত, নিরাপত্তা বজায় রাখা এবং অনুরোধের উত্তর দেওয়ার জন্য আমরা তথ্য ব্যবহার করি।',
      'নিজস্ব ঝুঁকি/ইতিহাসের সুবিধা সংশ্লিষ্ট দোকানের নিজস্ব ক্রেতা ও ডেলিভারির ইতিহাস ব্যবহার করে। বর্তমানে কোনো বাহ্যিক ঝুঁকি সেবাদাতা নিবন্ধিত নেই, তাই বাহ্যিক ঝুঁকি সুবিধা এখন তৃতীয় পক্ষের কাছে ক্রেতা যাচাইয়ের অনুরোধ পাঠায় না। এটি শনাক্তযোগ্য ক্রেতার যৌথ কালোতালিকা নয়।',
      'কোনো দোকান নেটওয়ার্ক বিশ্লেষণে অংশ নিতে সম্মতি দিলে তার যোগ্য পরিচালনাগত ফল সমষ্টিগত বেঞ্চমার্কে অবদান রাখতে পারে। প্রকাশিত বেঞ্চমার্কে ন্যূনতম অংশগ্রহণ/নমুনার শর্ত, অবদানের সীমা এবং মোটা ধাপে রাউন্ডিং প্রয়োগ হয়; অপর্যাপ্ত দলের ফল প্রকাশ করা হয় না। এতে অন্য বিক্রেতার পরিচয়, পৃথক ক্রেতার রেকর্ড বা দোকানভিত্তিক সঠিক ফল প্রকাশ পায় না। Network intelligence সেটিংস থেকে অংশগ্রহণ পরিবর্তন করা যায়; আগে প্রকাশিত পরিচয়বিহীন সমষ্টিগত ফল পৃথক ক্রেতার রেকর্ড নয়।'
    ] }
  },
  {
    id: 'providers',
    en: { title: '8. Sharing and service providers', paragraphs: [
      'Information is available to authorised members of the relevant shop according to their roles. Restricted platform support/administration access is used for service operation and support; customer phone details are masked by default, with specially authorised, reason-recorded access for supported investigations.',
      'Supabase provides account authentication and the hosted PostgreSQL business database. Google provides Google Sign-In when chosen, and Firebase Cloud Messaging provides push delivery. Cloudflare R2 stores private uploaded import/payout files and export files. Northflank hosts the backend API, background processing and Redis infrastructure. Vercel hosts the web application, and Cloudflare provides domain/network infrastructure. These services process the information needed for those functions, including relevant connection metadata.',
      'Couriers, commerce platforms, messaging providers and seller-designated API/webhook destinations receive information only in the circumstances described in section 6. Their handling of information in their own services is also governed by their own terms and privacy policies.',
      'We do not sell personal information or share it with advertisers for advertising. Information may be disclosed where required by an applicable legal obligation or valid legal request, or as necessary to investigate abuse, protect security and resolve disputes.'
    ] },
    bn: { title: '৮. তথ্য শেয়ার ও সেবাদাতা', paragraphs: [
      'সংশ্লিষ্ট দোকানের অনুমোদিত সদস্যরা তাঁদের ভূমিকা অনুযায়ী তথ্য পান। সেবা পরিচালনা ও সহায়তার জন্য প্ল্যাটফর্মের সহায়তা/প্রশাসনিক অ্যাক্সেস সীমিত থাকে; ক্রেতার ফোন সাধারণত আংশিক আড়াল করা থাকে, এবং প্রয়োজনীয় তদন্তে বিশেষ অনুমোদন ও নথিভুক্ত কারণের ভিত্তিতে তা দেখার ব্যবস্থা আছে।',
      'Supabase অ্যাকাউন্টের পরিচয় যাচাই এবং হোস্ট করা PostgreSQL ব্যবসায়িক ডেটাবেস সরবরাহ করে। বেছে নেওয়া হলে Google, Google Sign-In দেয় এবং Firebase Cloud Messaging পুশ পৌঁছে দেয়। Cloudflare R2 ব্যক্তিগত আপলোড করা আমদানি/পেআউট ফাইল এবং এক্সপোর্ট ফাইল রাখে। Northflank ব্যাকএন্ড API, পটভূমির কাজ ও Redis অবকাঠামো হোস্ট করে। Vercel ওয়েব অ্যাপ্লিকেশন হোস্ট করে এবং Cloudflare ডোমেইন/নেটওয়ার্ক অবকাঠামো দেয়। এসব কাজের জন্য প্রয়োজনীয় তথ্য, প্রাসঙ্গিক সংযোগের মেটাডেটাসহ, এই সেবাগুলো প্রক্রিয়া করে।',
      'কুরিয়ার, বাণিজ্য প্ল্যাটফর্ম, বার্তা সেবাদাতা এবং বিক্রেতার নির্ধারিত API/webhook গন্তব্য কেবল ৬ নম্বর অংশে বর্ণিত পরিস্থিতিতে তথ্য পায়। তাঁদের নিজস্ব সেবায় তথ্য ব্যবহারের ক্ষেত্রে তাঁদের নিজস্ব শর্ত ও গোপনীয়তা নীতিও প্রযোজ্য।',
      'আমরা ব্যক্তিগত তথ্য বিক্রি করি না বা বিজ্ঞাপনের জন্য বিজ্ঞাপনদাতাকে দিই না। প্রযোজ্য আইনি বাধ্যবাধকতা বা বৈধ আইনি অনুরোধে, অথবা অপব্যবহার তদন্ত, নিরাপত্তা রক্ষা ও বিরোধ মেটাতে প্রয়োজন হলে তথ্য প্রকাশ করা হতে পারে।'
    ] }
  },
  {
    id: 'international',
    en: { title: '9. International processing', paragraphs: [
      'Although ecomsbd focuses on Bangladesh, our cloud, authentication and delivery providers operate internationally. Information may be processed or stored outside Bangladesh where those providers operate. We do not promise Bangladesh-only storage. The applicable services and their role in processing information are described above.'
    ] },
    bn: { title: '৯. আন্তর্জাতিকভাবে তথ্য প্রক্রিয়াকরণ', paragraphs: [
      'ecomsbd বাংলাদেশকে কেন্দ্র করে তৈরি হলেও আমাদের ক্লাউড, পরিচয় যাচাই ও বার্তা পৌঁছানোর সেবাদাতারা আন্তর্জাতিকভাবে কাজ করেন। তাঁরা যেখানে কাজ করেন, সেখানে বাংলাদেশের বাইরে তথ্য প্রক্রিয়া বা সংরক্ষণ হতে পারে। শুধু বাংলাদেশে তথ্য রাখার প্রতিশ্রুতি আমরা দিই না। প্রযোজ্য সেবা ও তথ্য প্রক্রিয়াকরণে তাঁদের ভূমিকা ওপরে বলা হয়েছে।'
    ] }
  },
  {
    id: 'retention',
    en: { title: '10. Retention and account deletion', paragraphs: [
      'We retain information for as long as necessary for the purposes described in this policy, including operating the service, security, reconciliation, dispute resolution, applicable legal obligations and enforcement. There is no single fixed retention period for all records. Export downloads expire; the export history and audit records may remain after the downloadable content is removed.',
      'A shop owner can initiate deletion in the mobile app under Settings → Your data and privacy → Closing your account, where available. The current workflow schedules deletion after a 14-day cooling-off period, displays the scheduled date and allows cancellation during that period. Access continues until the deletion workflow runs. This workflow is shop-specific: deleting one shop does not delete a separate active shop or an account still needed for membership in another shop.',
      'When the workflow completes, it anonymises customer profile names, phone details, addresses and notes; clears specified CRM free text and message content/recipient details; removes device push tokens; revokes shop sessions and memberships; and clears the shop name and pickup contact/address. It removes import/export objects from R2. For members with no other active shop membership, it clears account contact details and deletes their linked Supabase authentication identity.',
      'The automated workflow does not erase every stored copy. Financial ledger, receivable, payout and profit records, audit history and payout source evidence remain for reconciliation, accountability and disputes. Other retained operational records can include order/address snapshots, original imported or pasted information, integration/event records, supplier records and connection configuration; some can still contain personal information. We therefore do not describe all retained records as anonymous. Further access or erasure requests require review of the affected records and any justified retention need.',
      'Deleting an ecomsbd shop does not itself delete information already held independently by a courier, connected store or other provider, or copies you have downloaded. Contact the relevant provider for its own deletion controls.'
    ] },
    bn: { title: '১০. তথ্য সংরক্ষণ ও অ্যাকাউন্ট মুছে ফেলা', paragraphs: [
      'এই নীতিতে বর্ণিত উদ্দেশ্যে যত দিন প্রয়োজন, আমরা তথ্য রাখি; এর মধ্যে সেবা পরিচালনা, নিরাপত্তা, হিসাব মেলানো, বিরোধ নিষ্পত্তি, প্রযোজ্য আইনি বাধ্যবাধকতা ও শর্ত কার্যকর করা অন্তর্ভুক্ত। সব রেকর্ডের জন্য একটিমাত্র নির্দিষ্ট সংরক্ষণকাল নেই। এক্সপোর্ট ডাউনলোডের মেয়াদ শেষ হয়; ডাউনলোডযোগ্য বিষয়বস্তু সরানোর পরও এক্সপোর্টের ইতিহাস ও অডিট রেকর্ড থাকতে পারে।',
      'সুবিধাটি উপলব্ধ থাকলে দোকানের মালিক মোবাইল অ্যাপের সেটিংস → আপনার তথ্য ও প্রাইভেসি → অ্যাকাউন্ট বন্ধ করা থেকে মুছে ফেলার প্রক্রিয়া শুরু করতে পারেন। বর্তমান প্রক্রিয়ায় ১৪ দিনের সিদ্ধান্ত পুনর্বিবেচনার সময়ের পরে মুছে ফেলার সময় নির্ধারণ করা হয়, তারিখ দেখানো হয় এবং ওই সময়ের মধ্যে অনুরোধ বাতিল করা যায়। মুছে ফেলার প্রক্রিয়া চালানো পর্যন্ত অ্যাক্সেস থাকে। এটি নির্দিষ্ট দোকানের জন্য: একটি দোকান মুছলে অন্য সক্রিয় দোকান বা অন্য দোকানের সদস্যপদের জন্য প্রয়োজনীয় অ্যাকাউন্ট মুছে যায় না।',
      'প্রক্রিয়া শেষ হলে ক্রেতার প্রোফাইলের নাম, ফোনের তথ্য, ঠিকানা ও নোট পরিচয়মুক্ত করা হয়; নির্দিষ্ট CRM লেখা এবং বার্তার বিষয়বস্তু/প্রাপকের তথ্য পরিষ্কার করা হয়; ডিভাইসের পুশ টোকেন সরানো হয়; দোকানের সেশন ও সদস্যপদ প্রত্যাহার করা হয়; এবং দোকানের নাম ও পিকআপ যোগাযোগ/ঠিকানা পরিষ্কার করা হয়। R2 থেকে আমদানি/এক্সপোর্ট অবজেক্ট সরানো হয়। অন্য কোনো সক্রিয় দোকানে সদস্যপদ না থাকা সদস্যদের অ্যাকাউন্টের যোগাযোগের তথ্য পরিষ্কার এবং সংযুক্ত Supabase পরিচয় যাচাইয়ের অ্যাকাউন্ট মুছে ফেলা হয়।',
      'স্বয়ংক্রিয় প্রক্রিয়া সংরক্ষিত প্রতিটি কপি মুছে দেয় না। হিসাব মেলানো, জবাবদিহি ও বিরোধের জন্য আর্থিক লেজার, পাওনা, পেআউট ও লাভের রেকর্ড, অডিট ইতিহাস এবং পেআউটের মূল প্রমাণ থাকে। অন্য সংরক্ষিত পরিচালনাগত রেকর্ডে অর্ডার/ঠিকানার সেই সময়ের কপি, মূল আমদানি বা পেস্ট করা তথ্য, ইন্টিগ্রেশন/ইভেন্ট রেকর্ড, সরবরাহকারীর রেকর্ড ও সংযোগের কনফিগারেশন থাকতে পারে; কিছুতে তখনও ব্যক্তিগত তথ্য থাকতে পারে। তাই সংরক্ষিত সব রেকর্ডকে আমরা পরিচয়বিহীন বলি না। অতিরিক্ত তথ্য দেখার বা মুছে ফেলার অনুরোধে সংশ্লিষ্ট রেকর্ড ও সংরক্ষণের ন্যায্য প্রয়োজন পর্যালোচনা করতে হয়।',
      'ecomsbd-এর দোকান মুছলে কুরিয়ার, সংযুক্ত স্টোর বা অন্য সেবাদাতার স্বাধীনভাবে রাখা তথ্য কিংবা আপনার ডাউনলোড করা কপি নিজে থেকে মুছে যায় না। তাঁদের নিজস্ব মুছে ফেলার ব্যবস্থার জন্য সংশ্লিষ্ট সেবাদাতার সঙ্গে যোগাযোগ করুন।'
    ] }
  },
  {
    id: 'controls',
    en: { title: '11. Access, correction and controls', paragraphs: [
      'You can view your information and use the available account, shop and customer editing controls, subject to your role. Authorised users can export supported datasets from Data & privacy in the mobile app; customer exports require the appropriate owner permission. You can review/revoke devices and sessions, manage team roles, disconnect available integrations, revoke API keys and change network participation. Disconnecting a service stops the enabled connection; it does not automatically erase historical records.',
      'For information or corrections not covered by these controls, use the privacy contact below. We may need to verify your identity and authority over the shop before providing information or acting on a request. If you are a shop’s customer, identify the relevant shop so your request can be directed to the seller responsible for the record.'
    ] },
    bn: { title: '১১. তথ্য দেখা, সংশোধন ও নিয়ন্ত্রণ', paragraphs: [
      'আপনার ভূমিকার অনুমতি অনুযায়ী আপনি তথ্য দেখতে এবং উপলব্ধ অ্যাকাউন্ট, দোকান ও ক্রেতার তথ্য সম্পাদনার ব্যবস্থা ব্যবহার করতে পারেন। অনুমোদিত ব্যবহারকারীরা মোবাইল অ্যাপের Data & privacy থেকে সমর্থিত তথ্য এক্সপোর্ট করতে পারেন; ক্রেতার তথ্য এক্সপোর্টে মালিকের উপযুক্ত অনুমতি লাগে। ডিভাইস ও সেশন দেখা/প্রত্যাহার, দলের ভূমিকা পরিচালনা, উপলব্ধ ইন্টিগ্রেশন বিচ্ছিন্ন করা, API key প্রত্যাহার এবং নেটওয়ার্কে অংশগ্রহণ পরিবর্তন করা যায়। সেবা বিচ্ছিন্ন করলে চালু সংযোগ বন্ধ হয়; পুরোনো রেকর্ড স্বয়ংক্রিয়ভাবে মুছে যায় না।',
      'এই ব্যবস্থাগুলোর বাইরে তথ্য বা সংশোধনের জন্য নিচের গোপনীয়তা-সংক্রান্ত যোগাযোগ ব্যবহার করুন। তথ্য দেওয়া বা অনুরোধ কার্যকর করার আগে আপনার পরিচয় ও দোকানের ওপর কর্তৃত্ব যাচাই করতে হতে পারে। আপনি কোনো দোকানের ক্রেতা হলে সংশ্লিষ্ট দোকানের পরিচয় দিন, যাতে অনুরোধ রেকর্ডটির দায়িত্বে থাকা বিক্রেতার কাছে পাঠানো যায়।'
    ] }
  },
  {
    id: 'security',
    en: { title: '12. Security and connected-service credentials', paragraphs: [
      'We use HTTPS, authenticated access, shop-scoped permissions, restricted administrative access and logging safeguards to protect information. Stored customer phone fields and connected-service credentials use application-level encryption where implemented. Credential APIs return connection status or masked indicators instead of returning stored secrets in plaintext. Newly generated API/webhook secrets may be shown once so the authorised seller can configure their connection.',
      'These measures reduce risk but do not guarantee absolute security or mean that every business field is encrypted at the application level. Keep your account and connected-service credentials private, use appropriate team permissions and revoke access when it is no longer needed.'
    ] },
    bn: { title: '১২. নিরাপত্তা ও সংযুক্ত সেবার ক্রেডেনশিয়াল', paragraphs: [
      'তথ্য সুরক্ষায় আমরা HTTPS, পরিচয় যাচাই করা অ্যাক্সেস, দোকানভিত্তিক অনুমতি, সীমিত প্রশাসনিক অ্যাক্সেস এবং লগের সুরক্ষা ব্যবস্থা ব্যবহার করি। বাস্তবায়িত ক্ষেত্রগুলোতে সংরক্ষিত ক্রেতার ফোন ও সংযুক্ত সেবার ক্রেডেনশিয়ালে অ্যাপ্লিকেশন-স্তরের এনক্রিপশন ব্যবহার হয়। ক্রেডেনশিয়াল API সংরক্ষিত গোপন তথ্য সরাসরি পাঠানোর বদলে সংযোগের অবস্থা বা আংশিক আড়াল করা নির্দেশক দেয়। অনুমোদিত বিক্রেতা সংযোগ সেট করতে পারেন বলে নতুন তৈরি API/webhook secret একবার দেখানো হতে পারে।',
      'এসব ব্যবস্থা ঝুঁকি কমায়, তবে সম্পূর্ণ নিরাপত্তার নিশ্চয়তা দেয় না বা ব্যবসার প্রতিটি তথ্য অ্যাপ্লিকেশন স্তরে এনক্রিপ্ট করা বোঝায় না। অ্যাকাউন্ট ও সংযুক্ত সেবার ক্রেডেনশিয়াল গোপন রাখুন, দলের উপযুক্ত অনুমতি ব্যবহার করুন এবং প্রয়োজন শেষ হলে অ্যাক্সেস প্রত্যাহার করুন।'
    ] }
  },
  {
    id: 'notifications',
    en: { title: '13. Notifications and messaging choices', paragraphs: [
      'Push notifications can carry operational alerts and relevant shop/order references. You can manage notification categories, push preferences and available quiet-hour settings in the app, and change notification permission in your device settings. Disabling push does not remove the shop’s underlying business records or in-app notification history.',
      'Customer messaging depends on the seller’s recorded transactional or marketing consent and the availability of the channel. Where supported, customers can use message opt-out/unsubscribe controls or ask the seller to stop messages. A marketing opt-out does not by itself delete an order or its required operational records.'
    ] },
    bn: { title: '১৩. নোটিফিকেশন ও বার্তার পছন্দ', paragraphs: [
      'পুশ নোটিফিকেশনে পরিচালনাগত সতর্কতা ও প্রাসঙ্গিক দোকান/অর্ডারের রেফারেন্স থাকতে পারে। অ্যাপে নোটিফিকেশনের বিভাগ, পুশের পছন্দ ও উপলব্ধ নীরব সময়ের সেটিংস এবং ডিভাইস সেটিংসে নোটিফিকেশনের অনুমতি পরিবর্তন করতে পারেন। পুশ বন্ধ করলে দোকানের মূল ব্যবসায়িক রেকর্ড বা অ্যাপের নোটিফিকেশনের ইতিহাস মুছে যায় না।',
      'ক্রেতাকে বার্তা পাঠানো বিক্রেতার নথিভুক্ত লেনদেন-সংক্রান্ত বা বিপণনের সম্মতি এবং চ্যানেলের প্রাপ্যতার ওপর নির্ভর করে। যেখানে ব্যবস্থা আছে, ক্রেতারা বার্তা বন্ধ/আনসাবস্ক্রাইব করতে বা বিক্রেতাকে বার্তা বন্ধ করতে বলতে পারেন। বিপণনের বার্তা বন্ধ করলে অর্ডার বা তার প্রয়োজনীয় পরিচালনাগত রেকর্ড নিজে থেকে মুছে যায় না।'
    ] }
  },
  {
    id: 'children',
    en: { title: '14. Children', paragraphs: [
      'ecomsbd is a seller/business operations service and is not directed to children. We do not ask for a date of birth as part of the account profile. If you believe a child has provided personal information through an account, contact us so we can review the circumstances and take appropriate action. Sellers remain responsible for the customer information they submit.'
    ] },
    bn: { title: '১৪. শিশু', paragraphs: [
      'ecomsbd বিক্রেতা/ব্যবসার কার্যক্রমের সেবা এবং শিশুদের উদ্দেশ্যে তৈরি নয়। অ্যাকাউন্ট প্রোফাইলে আমরা জন্মতারিখ চাই না। কোনো শিশু অ্যাকাউন্টের মাধ্যমে ব্যক্তিগত তথ্য দিয়েছে বলে মনে হলে আমাদের সঙ্গে যোগাযোগ করুন, যাতে পরিস্থিতি পর্যালোচনা করে উপযুক্ত ব্যবস্থা নিতে পারি। বিক্রেতার জমা দেওয়া ক্রেতার তথ্যের দায়িত্ব বিক্রেতারই থাকে।'
    ] }
  },
  {
    id: 'changes',
    en: { title: '15. Changes to this policy', paragraphs: [
      'We will update this page when our data practices change and revise the “Last updated” date. Where a change requires additional notice or permission, we will provide it through an appropriate service notice or consent flow before applying that change.'
    ] },
    bn: { title: '১৫. এই নীতির পরিবর্তন', paragraphs: [
      'আমাদের তথ্য ব্যবহারের পদ্ধতি বদলালে এই পৃষ্ঠা হালনাগাদ করব এবং “সর্বশেষ হালনাগাদ” তারিখ পরিবর্তন করব। কোনো পরিবর্তনে অতিরিক্ত নোটিশ বা অনুমতি প্রয়োজন হলে, তা কার্যকর করার আগে উপযুক্ত সেবার নোটিশ বা সম্মতি গ্রহণের মাধ্যমে জানানো হবে।'
    ] }
  }
] as const;
