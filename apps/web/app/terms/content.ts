// Paired translations keep the public, server-rendered Terms in sync.
// Status: draft, pending legal review (docs/launch/LAUNCH_STATUS.md).
import type { PolicyLocale } from '../privacy-policy/content';

export const termsUpdated = '2026-11-25';

export const termsSections: {
  id: string;
  en: { title: string; paragraphs: string[] };
  bn: { title: string; paragraphs: string[] };
}[] = [
  {
    id: 'about',
    en: { title: '1. About these terms', paragraphs: [
      'These Terms of Service govern your use of ecomsbd: the Android app (com.ecomsbd.app), the web platform at ecomsbd.com and its workspace at app.ecomsbd.com, the browser extension, and the related services. “We”, “us” and “our” refer to the operation of ecomsbd. “You” means the person who creates an account or uses the service, and the shop or business you use it for.',
      'By creating an account or using the service you agree to these terms and to our Privacy Policy. If you use ecomsbd for a business, you confirm that you may accept these terms for it. You must be at least 18 years old to create an account.'
    ] },
    bn: { title: '১. এই শর্তাবলি সম্পর্কে', paragraphs: [
      'ecomsbd ব্যবহারের ক্ষেত্রে এই সেবার শর্তাবলি প্রযোজ্য: Android অ্যাপ (com.ecomsbd.app), ecomsbd.com ওয়েব প্ল্যাটফর্ম ও এর ওয়ার্কস্পেস app.ecomsbd.com, ব্রাউজার এক্সটেনশন এবং সংশ্লিষ্ট সেবা। “আমরা” ও “আমাদের” বলতে ecomsbd-এর পরিচালনাকে বোঝানো হয়েছে। “আপনি” বলতে যিনি অ্যাকাউন্ট খোলেন বা সেবা ব্যবহার করেন তাঁকে এবং যে দোকান বা ব্যবসার জন্য ব্যবহার করেন সেটিকে বোঝানো হয়েছে।',
      'অ্যাকাউন্ট খুললে বা সেবা ব্যবহার করলে আপনি এই শর্তাবলি এবং আমাদের গোপনীয়তা নীতিতে সম্মত হচ্ছেন। কোনো ব্যবসার জন্য ecomsbd ব্যবহার করলে আপনি নিশ্চিত করছেন যে সেই ব্যবসার পক্ষে এই শর্তাবলি মেনে নেওয়ার অধিকার আপনার আছে। অ্যাকাউন্ট খুলতে আপনার বয়স কমপক্ষে ১৮ বছর হতে হবে।'
    ] }
  },
  {
    id: 'service',
    en: { title: '2. What ecomsbd does', paragraphs: [
      'ecomsbd is a tool for sellers in Bangladesh to run their shop: turn chat messages into orders, keep customers, products and stock, confirm orders with customers, book parcels with the seller’s own courier accounts, print invoices and labels, follow cash on delivery (COD) and payouts, and see their figures.',
      'ecomsbd is software. It is not a courier, a payment service or a marketplace. It does not sell goods, carry parcels or collect money from your customers, and it is not a party to the sale between you and your customer.'
    ] },
    bn: { title: '২. ecomsbd কী করে', paragraphs: [
      'ecomsbd বাংলাদেশের বিক্রেতাদের দোকান চালানোর একটি টুল: চ্যাটের মেসেজ থেকে অর্ডার তৈরি, ক্রেতা, পণ্য ও স্টকের হিসাব রাখা, ক্রেতার সঙ্গে অর্ডার নিশ্চিত করা, বিক্রেতার নিজের কুরিয়ার অ্যাকাউন্টে পার্সেল বুক করা, চালান ও লেবেল প্রিন্ট, ক্যাশ অন ডেলিভারি (COD) ও পেআউটের হিসাব এবং ব্যবসার সংখ্যাগুলো দেখা।',
      'ecomsbd একটি সফটওয়্যার। এটি কুরিয়ার, পেমেন্ট সেবা বা মার্কেটপ্লেস নয়। ecomsbd পণ্য বিক্রি করে না, পার্সেল বহন করে না, আপনার ক্রেতার কাছ থেকে টাকা নেয় না এবং আপনার ও আপনার ক্রেতার মধ্যকার কেনাবেচার কোনো পক্ষ নয়।'
    ] }
  },
  {
    id: 'free-launch',
    en: { title: '3. Free Launch Mode and pricing', paragraphs: [
      'ecomsbd currently runs in Free Launch Mode: every feature is free and billing is switched off. We do not ask for card, bKash or Nagad details for an ecomsbd plan.',
      'Free Launch Mode may end. Before any paid plan applies to your shop we will tell you in the app or by email, with the price and the date, and nothing is charged unless you choose a paid plan. Costs charged by other companies, such as your courier’s delivery and COD charges, are between you and them.',
      'Features may be added, changed, limited or switched off during the launch, for example while a feature waits for a provider’s approval or is tested with a few shops first.'
    ] },
    bn: { title: '৩. ফ্রি লঞ্চ মোড ও মূল্য', paragraphs: [
      'ecomsbd বর্তমানে ফ্রি লঞ্চ মোডে চলছে: সব ফিচার বিনামূল্যে এবং বিলিং বন্ধ। ecomsbd প্ল্যানের জন্য আমরা কার্ড, bKash বা Nagad-এর তথ্য চাই না।',
      'ফ্রি লঞ্চ মোড শেষ হতে পারে। আপনার দোকানে কোনো পেইড প্ল্যান চালুর আগে আমরা অ্যাপে বা ইমেইলে দাম ও তারিখসহ জানাব, এবং আপনি নিজে পেইড প্ল্যান না বাছলে কোনো টাকা কাটা হবে না। অন্য প্রতিষ্ঠানের চার্জ, যেমন আপনার কুরিয়ারের ডেলিভারি ও COD চার্জ, আপনার ও তাদের মধ্যকার বিষয়।',
      'লঞ্চের সময় ফিচার যোগ, পরিবর্তন, সীমিত বা বন্ধ হতে পারে, যেমন কোনো প্রোভাইডারের অনুমোদনের অপেক্ষায় থাকলে বা প্রথমে কয়েকটি দোকানে পরীক্ষা চলাকালে।'
    ] }
  },
  {
    id: 'account',
    en: { title: '4. Your account and your team', paragraphs: [
      'Keep your sign-in details and devices secure, and do not share your password or sign-in codes. You are responsible for what happens in your shop under your account and the accounts of team members you invite. The shop owner decides who joins the shop and what each role may see or change.',
      'Tell us at once if you think someone else has used your account. The information you give us about yourself and your shop should be true and kept up to date.'
    ] },
    bn: { title: '৪. আপনার অ্যাকাউন্ট ও আপনার দল', paragraphs: [
      'সাইন-ইনের তথ্য ও ডিভাইস নিরাপদ রাখুন এবং পাসওয়ার্ড বা সাইন-ইন কোড কাউকে দেবেন না। আপনার অ্যাকাউন্ট এবং আপনার আমন্ত্রণ জানানো দলের সদস্যদের অ্যাকাউন্টে আপনার দোকানে যা ঘটে, তার দায়িত্ব আপনার। কে দোকানে যোগ দেবেন এবং কোন ভূমিকা কী দেখতে বা বদলাতে পারবে, তা দোকানের মালিক ঠিক করেন।',
      'অন্য কেউ আপনার অ্যাকাউন্ট ব্যবহার করেছে মনে হলে সঙ্গে সঙ্গে আমাদের জানান। নিজের ও দোকানের সম্পর্কে যে তথ্য দেন, তা সত্য ও হালনাগাদ রাখুন।'
    ] }
  },
  {
    id: 'your-data',
    en: { title: '5. Your shop’s data belongs to you', paragraphs: [
      'The orders, customers, products, notes, files and other records you or your team put into ecomsbd belong to you. You give us permission to store and process them only to run the service for your shop, keep it secure, and fix problems, as described in our Privacy Policy. We do not sell your data, and we do not show your customers’ details to other sellers.',
      'You can export your records from the app, and you can ask us to delete your account and its data (see the account deletion page). Some records may be kept for a limited time where the law or security requires it, as the Privacy Policy explains.',
      'Your customers’ information is in ecomsbd because you put it there. You are responsible for having a lawful reason to use it, for telling your customers what they need to know, and for answering their requests about their information.'
    ] },
    bn: { title: '৫. আপনার দোকানের তথ্য আপনার', paragraphs: [
      'আপনি বা আপনার দল ecomsbd-এ যে অর্ডার, ক্রেতা, পণ্য, নোট, ফাইল ও অন্যান্য রেকর্ড রাখেন, সেগুলো আপনার। আমাদের গোপনীয়তা নীতিতে যেমন বলা আছে, শুধু আপনার দোকানের জন্য সেবা চালানো, নিরাপদ রাখা ও সমস্যা ঠিক করার জন্য সেগুলো সংরক্ষণ ও প্রক্রিয়া করার অনুমতি আপনি আমাদের দিচ্ছেন। আমরা আপনার তথ্য বিক্রি করি না এবং আপনার ক্রেতার তথ্য অন্য বিক্রেতাকে দেখাই না।',
      'অ্যাপ থেকে আপনি রেকর্ড এক্সপোর্ট করতে পারেন এবং অ্যাকাউন্ট ও তার তথ্য মুছে ফেলার অনুরোধ করতে পারেন (অ্যাকাউন্ট মুছে ফেলার পৃষ্ঠা দেখুন)। আইন বা নিরাপত্তার প্রয়োজনে কিছু রেকর্ড সীমিত সময়ের জন্য রাখা হতে পারে, যা গোপনীয়তা নীতিতে বলা আছে।',
      'আপনার ক্রেতার তথ্য ecomsbd-এ আছে কারণ আপনি তা রেখেছেন। সেই তথ্য ব্যবহারের আইনসম্মত কারণ থাকা, ক্রেতাকে প্রয়োজনীয় বিষয় জানানো এবং তাঁদের তথ্য নিয়ে অনুরোধের উত্তর দেওয়ার দায়িত্ব আপনার।'
    ] }
  },
  {
    id: 'couriers',
    en: { title: '6. Courier accounts are yours', paragraphs: [
      'When you connect a courier (such as Steadfast, Pathao or RedX), you use your own account with that courier, under your own agreement with them. Your API keys are stored encrypted and used only for the actions you take or turn on for your shop, such as booking a parcel, checking its status or reading your balance. Saving an order never books a courier by itself; a booking is your action.',
      'The courier, not ecomsbd, carries the parcel, collects the COD from your customer and pays it to you. Delivery charges, COD charges, payouts, claims and disputes are between you and your courier. If the courier rejects your saved key, ecomsbd shows that the account needs reconnecting and pauses that account’s bookings and status updates until you enter a working key.'
    ] },
    bn: { title: '৬. কুরিয়ার অ্যাকাউন্ট আপনার', paragraphs: [
      'কোনো কুরিয়ার (যেমন Steadfast, Pathao বা RedX) যুক্ত করলে আপনি সেই কুরিয়ারে আপনার নিজের অ্যাকাউন্ট, তাদের সঙ্গে আপনার নিজের চুক্তির অধীনে ব্যবহার করেন। আপনার API key এনক্রিপ্ট করে রাখা হয় এবং শুধু আপনার নেওয়া বা আপনার দোকানে চালু করা কাজে ব্যবহার হয়, যেমন পার্সেল বুক করা, অবস্থা দেখা বা ব্যালান্স দেখা। অর্ডার সেভ করলে নিজে থেকে কখনো কুরিয়ার বুক হয় না; বুকিং আপনার কাজ।',
      'পার্সেল বহন করে, আপনার ক্রেতার কাছ থেকে COD নেয় এবং আপনাকে টাকা দেয় কুরিয়ার, ecomsbd নয়। ডেলিভারি চার্জ, COD চার্জ, পেআউট, দাবি ও বিরোধ আপনার ও আপনার কুরিয়ারের মধ্যকার বিষয়। কুরিয়ার আপনার সংরক্ষিত key গ্রহণ না করলে ecomsbd দেখায় যে অ্যাকাউন্টটি আবার যুক্ত করতে হবে, এবং কার্যকর key না দেওয়া পর্যন্ত সেই অ্যাকাউন্টে বুকিং ও স্ট্যাটাস আপডেট বন্ধ থাকে।'
    ] }
  },
  {
    id: 'no-delivery-guarantee',
    en: { title: '7. No guarantee of delivery', paragraphs: [
      'We do not guarantee that a courier accepts a booking, picks up or delivers a parcel, delivers it on time, collects the right amount, or pays you. Parcel statuses come from the courier; some couriers are checked on a schedule (about every 10 minutes) rather than sending updates, so a status in ecomsbd can be late or differ from the courier’s own panel. Check the courier’s panel for anything that matters to your money.',
      'Delivery-charge and COD figures that ecomsbd works out (from your prices, delivery rules and courier quotes) are help for you to check, not a promise of what a courier will charge or pay.'
    ] },
    bn: { title: '৭. ডেলিভারির কোনো নিশ্চয়তা নেই', paragraphs: [
      'কুরিয়ার বুকিং গ্রহণ করবে, পার্সেল তুলবে বা পৌঁছে দেবে, সময়মতো দেবে, সঠিক টাকা নেবে বা আপনাকে টাকা দেবে—এর কোনো নিশ্চয়তা আমরা দিই না। পার্সেলের অবস্থা আসে কুরিয়ার থেকে; কিছু কুরিয়ার নিজে আপডেট পাঠায় না, তাই নির্দিষ্ট সময় পরপর (প্রায় ১০ মিনিট) দেখা হয়। ফলে ecomsbd-এ দেখানো অবস্থা দেরিতে আসতে পারে বা কুরিয়ারের প্যানেল থেকে আলাদা হতে পারে। টাকার জন্য গুরুত্বপূর্ণ যেকোনো বিষয় কুরিয়ারের প্যানেলে দেখে নিন।',
      'ecomsbd যে ডেলিভারি চার্জ ও COD হিসাব করে দেয় (আপনার দাম, ডেলিভারি নিয়ম ও কুরিয়ারের কোট থেকে), সেগুলো আপনার যাচাইয়ের সুবিধার জন্য; কুরিয়ার কত চার্জ নেবে বা দেবে তার প্রতিশ্রুতি নয়।'
    ] }
  },
  {
    id: 'messages',
    en: { title: '8. Messages to your customers', paragraphs: [
      'When you turn on SMS updates or confirmation links, ecomsbd sends those messages to your customers on your shop’s behalf, through the SMS provider we use. You are responsible for having your customers’ agreement to receive them and for what you choose to send. Order detection from chats works only on the chat apps you choose, and nothing from a chat becomes an order until you create or approve it.',
      'You must not use ecomsbd to send spam, misleading messages or anything your customer did not ask for.'
    ] },
    bn: { title: '৮. আপনার ক্রেতাকে পাঠানো মেসেজ', paragraphs: [
      'আপনি SMS আপডেট বা নিশ্চিতকরণ লিংক চালু করলে ecomsbd আমাদের ব্যবহৃত SMS প্রোভাইডারের মাধ্যমে আপনার দোকানের পক্ষে আপনার ক্রেতাকে সেই মেসেজ পাঠায়। ক্রেতা এসব মেসেজ পেতে রাজি কি না এবং আপনি কী পাঠাবেন, তার দায়িত্ব আপনার। চ্যাট থেকে অর্ডার শনাক্তকরণ শুধু আপনার বেছে নেওয়া চ্যাট অ্যাপে কাজ করে, এবং আপনি তৈরি বা অনুমোদন না করা পর্যন্ত চ্যাটের কিছুই অর্ডার হয় না।',
      'স্প্যাম, বিভ্রান্তিকর মেসেজ বা ক্রেতা চাননি এমন কিছু পাঠাতে ecomsbd ব্যবহার করা যাবে না।'
    ] }
  },
  {
    id: 'acceptable-use',
    en: { title: '9. Acceptable use', paragraphs: [
      'Do not use ecomsbd to sell or ship anything illegal in Bangladesh, to defraud customers, couriers or other people, or to collect or use other people’s information without a lawful reason.',
      'Do not try to break into, overload, copy, scrape or reverse-engineer the service, get around its limits or security, or reach another shop’s data. Do not resell access to ecomsbd or use it to build a competing service without our written permission.'
    ] },
    bn: { title: '৯. গ্রহণযোগ্য ব্যবহার', paragraphs: [
      'বাংলাদেশে অবৈধ কোনো কিছু বিক্রি বা পাঠাতে, ক্রেতা, কুরিয়ার বা অন্য কাউকে প্রতারণা করতে, অথবা আইনসম্মত কারণ ছাড়া অন্যের তথ্য সংগ্রহ বা ব্যবহার করতে ecomsbd ব্যবহার করবেন না।',
      'সেবায় অনধিকার প্রবেশ, অতিরিক্ত চাপ দেওয়া, কপি, স্ক্র্যাপ বা রিভার্স-ইঞ্জিনিয়ার করার চেষ্টা করবেন না; এর সীমা বা নিরাপত্তা এড়াবেন না এবং অন্য দোকানের তথ্যে পৌঁছানোর চেষ্টা করবেন না। আমাদের লিখিত অনুমতি ছাড়া ecomsbd-এর অ্যাক্সেস পুনরায় বিক্রি করবেন না বা প্রতিযোগী সেবা তৈরিতে এটি ব্যবহার করবেন না।'
    ] }
  },
  {
    id: 'other-services',
    en: { title: '10. Other companies’ services', paragraphs: [
      'ecomsbd works with services run by other companies: couriers, the SMS provider, Google and Supabase for sign-in, Meta (Messenger and WhatsApp) where connected, app stores and hosting. Their own terms apply to your use of them, and we are not responsible for their services, changes or outages.'
    ] },
    bn: { title: '১০. অন্য প্রতিষ্ঠানের সেবা', paragraphs: [
      'ecomsbd অন্য প্রতিষ্ঠানের চালানো সেবার সঙ্গে কাজ করে: কুরিয়ার, SMS প্রোভাইডার, সাইন-ইনের জন্য Google ও Supabase, যুক্ত থাকলে Meta (Messenger ও WhatsApp), অ্যাপ স্টোর ও হোস্টিং। সেগুলো ব্যবহারে তাদের নিজস্ব শর্ত প্রযোজ্য, এবং তাদের সেবা, পরিবর্তন বা বিভ্রাটের দায় আমাদের নয়।'
    ] }
  },
  {
    id: 'availability',
    en: { title: '11. Availability and changes', paragraphs: [
      'We work to keep ecomsbd running and your data safe, but the service is provided “as is” and “as available”. It may be unavailable at times for maintenance, updates or reasons outside our control, and it may contain mistakes. Keep your own copy of anything you cannot afford to lose, and check important figures before you act on them.',
      'We may change, add or remove features. If a change takes away something you rely on, we will try to tell you in advance.'
    ] },
    bn: { title: '১১. সেবার প্রাপ্যতা ও পরিবর্তন', paragraphs: [
      'ecomsbd চালু রাখতে এবং আপনার তথ্য নিরাপদ রাখতে আমরা চেষ্টা করি, তবে সেবাটি “যেমন আছে” ও “যখন পাওয়া যায়” ভিত্তিতে দেওয়া হয়। রক্ষণাবেক্ষণ, হালনাগাদ বা আমাদের নিয়ন্ত্রণের বাইরের কারণে মাঝে মাঝে সেবা বন্ধ থাকতে পারে এবং এতে ভুল থাকতে পারে। যা হারালে আপনার ক্ষতি, তার নিজস্ব কপি রাখুন এবং কাজে লাগানোর আগে গুরুত্বপূর্ণ সংখ্যাগুলো যাচাই করুন।',
      'আমরা ফিচার পরিবর্তন, যোগ বা বাদ দিতে পারি। কোনো পরিবর্তনে আপনার নির্ভর করা কিছু চলে গেলে আমরা আগে থেকে জানানোর চেষ্টা করব।'
    ] }
  },
  {
    id: 'liability',
    en: { title: '12. Limits of our responsibility', paragraphs: [
      'To the extent the law of Bangladesh allows, ecomsbd is not responsible for lost sales, lost profit, lost or delayed parcels, courier charges or payouts, or other indirect loss arising from your use of the service, from a courier or other company’s service, or from information you entered. During Free Launch Mode the service is free, and our total responsibility to you for any claim is limited to the amount you paid us for the service in the 12 months before the claim, if any.',
      'Nothing in these terms removes rights you have by law that cannot be removed.'
    ] },
    bn: { title: '১২. আমাদের দায়ের সীমা', paragraphs: [
      'বাংলাদেশের আইন যতটুকু অনুমতি দেয়, সেবা ব্যবহার, কুরিয়ার বা অন্য প্রতিষ্ঠানের সেবা, অথবা আপনার দেওয়া তথ্য থেকে হওয়া বিক্রি হারানো, লাভ হারানো, পার্সেল হারানো বা দেরি, কুরিয়ারের চার্জ বা পেআউট, অথবা অন্য পরোক্ষ ক্ষতির দায় ecomsbd-এর নয়। ফ্রি লঞ্চ মোডে সেবা বিনামূল্যে; যেকোনো দাবির ক্ষেত্রে আপনার প্রতি আমাদের মোট দায়, দাবির আগের ১২ মাসে সেবার জন্য আপনি আমাদের যা পরিশোধ করেছেন (যদি করে থাকেন), তার মধ্যে সীমিত।',
      'আইনে আপনার যে অধিকার বাদ দেওয়া যায় না, এই শর্তাবলির কিছুই তা বাদ দেয় না।'
    ] }
  },
  {
    id: 'ending',
    en: { title: '13. Ending your use', paragraphs: [
      'You can stop using ecomsbd at any time and ask us to delete your account. We may suspend or close an account that breaks these terms, puts other people or the service at risk, or is required to be closed by law. Where we can, we will tell you why and give you a chance to export your data first.'
    ] },
    bn: { title: '১৩. ব্যবহার বন্ধ করা', paragraphs: [
      'আপনি যেকোনো সময় ecomsbd ব্যবহার বন্ধ করতে এবং অ্যাকাউন্ট মুছে ফেলার অনুরোধ করতে পারেন। এই শর্তাবলি ভাঙলে, অন্যদের বা সেবার ঝুঁকি তৈরি করলে, অথবা আইন অনুযায়ী প্রয়োজন হলে আমরা কোনো অ্যাকাউন্ট স্থগিত বা বন্ধ করতে পারি। সম্ভব হলে আমরা কারণ জানাব এবং আগে আপনার তথ্য এক্সপোর্ট করার সুযোগ দেব।'
    ] }
  },
  {
    id: 'changes',
    en: { title: '14. Changes to these terms and governing law', paragraphs: [
      'We may update these terms. The date at the top shows the latest version; for an important change we will tell you in the app or by email before it applies. If you keep using ecomsbd after a change applies, the new terms apply to you.',
      'These terms are governed by the laws of Bangladesh. If a part of them cannot be enforced, the rest still applies. The English and Bangla versions are meant to say the same thing.'
    ] },
    bn: { title: '১৪. শর্তাবলির পরিবর্তন ও প্রযোজ্য আইন', paragraphs: [
      'আমরা এই শর্তাবলি হালনাগাদ করতে পারি। ওপরের তারিখ সর্বশেষ সংস্করণ দেখায়; গুরুত্বপূর্ণ পরিবর্তনের ক্ষেত্রে তা কার্যকর হওয়ার আগে অ্যাপে বা ইমেইলে জানাব। পরিবর্তন কার্যকর হওয়ার পরও ecomsbd ব্যবহার করলে নতুন শর্তাবলি আপনার জন্য প্রযোজ্য হবে।',
      'এই শর্তাবলি বাংলাদেশের আইন অনুযায়ী পরিচালিত। কোনো অংশ কার্যকর করা না গেলে বাকি অংশ প্রযোজ্য থাকবে। ইংরেজি ও বাংলা সংস্করণ একই কথা বলার উদ্দেশ্যে লেখা।'
    ] }
  }
];

export const termsHeading: Record<PolicyLocale, { title: string; lead: string; date: string; eyebrow: string }> = {
  en: { title: 'Terms of Service', lead: 'The rules for using ecomsbd, in plain words.', date: '25 November 2026', eyebrow: 'TERMS' },
  bn: { title: 'সেবার শর্তাবলি', lead: 'ecomsbd ব্যবহারের নিয়ম, সহজ ভাষায়।', date: '২৫ নভেম্বর ২০২৬', eyebrow: 'শর্তাবলি' },
};
