/// Copy for Auto order detect and the quick-order sheet (V4 WP1.4).
///
/// The heads-up and sheet title keep the spec's Banglish ("order mone
/// hocche") in both languages: it is what sellers say.
library;

const intakeEn = <String, String>{
  'intake.title': 'Auto order detect',
  'intake.subtitle': 'Spot order chats from WhatsApp and Business Suite',
  'intake.notEnabled': 'Auto order detect is not turned on for your shop yet.',
  'intake.unsupported': 'Auto order detect works on Android phones.',
  'intake.loadFailed': 'Could not load Auto order detect.',
  'intake.disclosureTitle': 'What this reads and sends',
  'intake.disclosureRead':
      'Reads: notifications from the chat apps you turn on below — the chat '
      'name and the message text. Nothing else on your phone.',
  'intake.disclosureKeep':
      'Keeps: those messages stay on this phone, encrypted, for up to 25 '
      'minutes so an order typed over several messages is read together. '
      'Then they are deleted.',
  'intake.disclosureSend':
      'Sends: nothing by itself. When a chat looks like an order (a phone '
      'number plus order words), you get a notification. Only when you tap '
      'Create is that chat sent to ecomsbd to make a draft.',
  'intake.disclosureStop':
      'Stop: turn off Notification Access for ecomsbd in phone settings, or '
      'tap Unlink below.',
  'intake.sectionPhone': 'This phone',
  'intake.link': 'Link this phone',
  'intake.linkFailed': 'Could not link this phone.',
  'intake.linked': 'Phone linked.',
  'intake.revoked':
      'This phone was unlinked from your shop. Link it again to keep '
      'detecting orders.',
  'intake.enterCode': 'Have a code from the web? Enter it',
  'intake.codeTitle': 'Pairing code',
  'intake.codeHint': '6-digit code from ecomsbd on the web',
  'intake.codeInvalid': 'Enter the 6 digits',
  'intake.codeSubmit': 'Link',
  'intake.cancel': 'Cancel',
  'intake.accessOn': 'Notification Access is on',
  'intake.accessOff': 'Notification Access is off',
  'intake.openAccess': 'Turn on Notification Access',
  'intake.accessNote':
      'In the list, find "ecomsbd order detect" and turn it on, then come back.',
  'intake.notificationsOff':
      'ecomsbd notifications are off, so order alerts cannot show. Allow '
      'them in phone settings.',
  'intake.listening': 'Listening for orders',
  'intake.notListening': 'Not listening yet',
  'intake.lastHeartbeat': 'Last check-in: {time}',
  'intake.noHeartbeat': 'Waiting for the first check-in',
  'intake.sectionApps': 'Apps to read',
  'intake.appsNote':
      'Personal WhatsApp and Messenger start off because they carry private '
      'chats.',
  'intake.covered': 'Read by your official Meta connection',
  'intake.appsForbidden':
      'Only the shop owner or a manager can change which apps are read.',
  'intake.saved': 'Saved.',
  'intake.staleTitle': 'This phone has not checked in for a day',
  'intake.staleBody':
      'The phone\'s battery saver has probably stopped ecomsbd. Let it run in '
      'the background:',
  'intake.guide.xiaomi':
      'Security app → Permissions → Autostart → turn on ecomsbd. Then '
      'Settings → Apps → ecomsbd → Battery saver → No restrictions.',
  'intake.guide.oppo':
      'Settings → Apps → ecomsbd → Battery usage → allow Auto launch and '
      'background activity.',
  'intake.guide.realme':
      'Settings → Apps → ecomsbd → Battery usage → allow Auto launch and '
      'background activity.',
  'intake.guide.vivo':
      'Settings → Battery → Background power consumption → ecomsbd → Allow. '
      'Also i Manager → App manager → Autostart → ecomsbd on.',
  'intake.guide.samsung':
      'Settings → Apps → ecomsbd → Battery → Unrestricted. Also remove '
      'ecomsbd from Sleeping apps (Battery → Background usage limits).',
  'intake.guide.other':
      'Settings → Apps → ecomsbd → Battery → Unrestricted (or "Don\'t '
      'optimise").',
  'intake.openAppSettings': 'Open app settings',
  'intake.unlink': 'Unlink this phone',
  'intake.unlinked': 'Phone unlinked.',
  'qo.title': '{sender} — order mone hocche',
  'qo.sending': 'Making a draft…',
  'qo.duplicate': 'This looks like an order you already have.',
  'qo.attention':
      'This customer already has an order; the message is in Chat orders.',
  'qo.failed': 'Could not make the draft.',
  'qo.retry': 'Try again',
  'qo.close': 'Close',
  'qo.done': 'Done',
  'qo.expired':
      'This chat is no longer on the phone (chats close 25 minutes after the '
      'last message).',
  'qo.nothing': 'No chat to show.',
  'qo.signIn': 'Sign in to ecomsbd first.',
  'qo.openApp': 'Open ecomsbd',
  'qo.messages': 'Messages',
  'qo.checkMarked': 'Check the marked fields: the reader was not sure of them.',
  'qo.choosePhone':
      'The chat has more than one number. Choose the delivery number.',
  'qo.phone': 'Phone',
  'qo.name': 'Customer name',
  'qo.address': 'Delivery address',
  'qo.items': 'Items',
  'qo.item': 'Item',
  'qo.qty': 'Qty',
  'qo.price': 'Price (৳)',
  'qo.pick': 'Choose product',
  'qo.pickVariant': 'Choose size or colour',
  'qo.notInCatalog': 'Not in your catalogue; saved as written.',
  'qo.cod': 'COD to collect (৳)',
  'qo.codCatalog': 'Worked out from your prices and delivery charge. Check it.',
  'qo.codMismatch':
      'The amount in the chat does not match your prices and delivery '
      'charge. Check it with the customer.',
  'qo.zoneUnknown': 'Area not recognised: no delivery charge was added.',
  'qo.blocked': 'You blocked this customer.',
  'qo.knownCustomer': '{name} · {count} orders before',
  'qo.courierDefault': 'Courier: {courier} (your default)',
  'qo.courierHistory':
      'Courier: {courier} — {delivered} of {parcels} delivered in this area '
      '(your parcels)',
  'qo.courierNote':
      'Booking stays separate: saving sends nothing to a courier.',
  'qo.create': 'Create order',
  'qo.creating': 'Creating…',
  'qo.confirmCall': 'Confirm & call customer',
  'qo.confirmedInChat': 'Already confirmed in chat',
  'qo.confirmNote':
      'Confirm & call: we check with the customer before the order goes '
      'ahead. Already confirmed: the order is confirmed now.',
  'qo.saveDraft': 'Save draft',
  'qo.saveDraftNote':
      'The draft stays in Chat orders. Changes made here are not kept.',
  'qo.more': 'Aro message lagbe',
  'qo.moreTitle': 'Add more messages',
  'qo.moreHint': 'Copy the messages in the chat, then paste them here.',
  'qo.moreAdd': 'Add',
  'qo.moreEmpty': 'Paste the messages first.',
  'qo.cancel': 'Cancel',
  'qo.phoneRequired': 'Enter the delivery phone number',
  'qo.itemsRequired': 'Add at least one item.',
  'qo.chooseProduct': 'Choose which product the customer meant.',
  'qo.amountError': 'Enter an amount like 1250',
  'qo.ordered': 'Order {number} created.',
  'qo.orderedNoNumber': 'Order created.',
  'qo.confirming': 'We will confirm it with the customer.',
  'qo.confirmedNow': 'Marked as confirmed by the customer in chat.',
  'qo.queued':
      'No internet: the order is saved on this phone and is sent when you '
      'are back online.',
  'qo.closed': 'This chat order is already closed in Chat orders.',
};

const intakeBn = <String, String>{
  'intake.title': 'অটো অর্ডার শনাক্ত',
  'intake.subtitle': 'WhatsApp আর Business Suite-এর অর্ডার চ্যাট ধরুন',
  'intake.notEnabled': 'আপনার দোকানে অটো অর্ডার শনাক্ত এখনো চালু হয়নি।',
  'intake.unsupported': 'অটো অর্ডার শনাক্ত Android ফোনে কাজ করে।',
  'intake.loadFailed': 'অটো অর্ডার শনাক্ত লোড করা যায়নি।',
  'intake.disclosureTitle': 'কী পড়ে, কী পাঠায়',
  'intake.disclosureRead':
      'পড়ে: নিচে যে চ্যাট অ্যাপগুলো চালু করবেন, শুধু সেগুলোর নোটিফিকেশন — '
      'চ্যাটের নাম আর মেসেজের লেখা। ফোনের আর কিছু না।',
  'intake.disclosureKeep':
      'রাখে: মেসেজগুলো এই ফোনেই এনক্রিপ্ট করে সর্বোচ্চ ২৫ মিনিট থাকে, যাতে '
      'কয়েকটা মেসেজে লেখা অর্ডার একসাথে পড়া যায়। তারপর মুছে যায়।',
  'intake.disclosureSend':
      'পাঠায়: নিজে থেকে কিছুই না। কোনো চ্যাট অর্ডারের মতো দেখালে (ফোন নম্বর আর '
      'অর্ডারের কথা) আপনি একটা নোটিফিকেশন পাবেন। শুধু Create চাপলে সেই চ্যাট '
      'ড্রাফট বানাতে ecomsbd-তে যায়।',
  'intake.disclosureStop':
      'বন্ধ করতে: ফোনের সেটিংসে ecomsbd-এর Notification Access বন্ধ করুন, '
      'অথবা নিচে আনলিংক চাপুন।',
  'intake.sectionPhone': 'এই ফোন',
  'intake.link': 'এই ফোন লিংক করুন',
  'intake.linkFailed': 'ফোন লিংক করা যায়নি।',
  'intake.linked': 'ফোন লিংক হয়েছে।',
  'intake.revoked':
      'এই ফোন আপনার দোকান থেকে আনলিংক করা হয়েছে। অর্ডার ধরতে আবার লিংক করুন।',
  'intake.enterCode': 'ওয়েব থেকে কোড পেয়েছেন? এখানে দিন',
  'intake.codeTitle': 'পেয়ারিং কোড',
  'intake.codeHint': 'ওয়েবের ecomsbd থেকে ৬ অঙ্কের কোড',
  'intake.codeInvalid': '৬টি অঙ্ক দিন',
  'intake.codeSubmit': 'লিংক করুন',
  'intake.cancel': 'বাতিল',
  'intake.accessOn': 'Notification Access চালু আছে',
  'intake.accessOff': 'Notification Access বন্ধ আছে',
  'intake.openAccess': 'Notification Access চালু করুন',
  'intake.accessNote':
      'তালিকায় "ecomsbd অর্ডার শনাক্ত" খুঁজে চালু করুন, তারপর ফিরে আসুন।',
  'intake.notificationsOff':
      'ecomsbd-এর নোটিফিকেশন বন্ধ, তাই অর্ডারের অ্যালার্ট দেখানো যাবে না। '
      'ফোনের সেটিংসে চালু করুন।',
  'intake.listening': 'অর্ডারের জন্য শুনছে',
  'intake.notListening': 'এখনো শুনছে না',
  'intake.lastHeartbeat': 'শেষ চেক-ইন: {time}',
  'intake.noHeartbeat': 'প্রথম চেক-ইনের অপেক্ষায়',
  'intake.sectionApps': 'যে অ্যাপগুলো পড়বে',
  'intake.appsNote':
      'পার্সোনাল WhatsApp আর Messenger শুরুতে বন্ধ থাকে, কারণ ওখানে ব্যক্তিগত চ্যাট থাকে।',
  'intake.covered': 'আপনার অফিশিয়াল Meta কানেকশন এটা পড়ছে',
  'intake.appsForbidden':
      'কোন অ্যাপ পড়া হবে তা শুধু দোকানের মালিক বা ম্যানেজার বদলাতে পারেন।',
  'intake.saved': 'সেভ হয়েছে।',
  'intake.staleTitle': 'এই ফোন এক দিন ধরে চেক-ইন করেনি',
  'intake.staleBody':
      'সম্ভবত ফোনের ব্যাটারি সেভার ecomsbd বন্ধ করে দিয়েছে। ব্যাকগ্রাউন্ডে চলতে দিন:',
  'intake.guide.xiaomi':
      'Security অ্যাপ → Permissions → Autostart → ecomsbd চালু করুন। তারপর '
      'Settings → Apps → ecomsbd → Battery saver → No restrictions।',
  'intake.guide.oppo':
      'Settings → Apps → ecomsbd → Battery usage → Auto launch আর ব্যাকগ্রাউন্ড '
      'অ্যাক্টিভিটি চালু করুন।',
  'intake.guide.realme':
      'Settings → Apps → ecomsbd → Battery usage → Auto launch আর ব্যাকগ্রাউন্ড '
      'অ্যাক্টিভিটি চালু করুন।',
  'intake.guide.vivo':
      'Settings → Battery → Background power consumption → ecomsbd → Allow। '
      'আর i Manager → App manager → Autostart → ecomsbd চালু।',
  'intake.guide.samsung':
      'Settings → Apps → ecomsbd → Battery → Unrestricted। আর Sleeping apps '
      'থেকে ecomsbd সরান (Battery → Background usage limits)।',
  'intake.guide.other':
      'Settings → Apps → ecomsbd → Battery → Unrestricted (বা "Don\'t optimise")।',
  'intake.openAppSettings': 'অ্যাপ সেটিংস খুলুন',
  'intake.unlink': 'এই ফোন আনলিংক করুন',
  'intake.unlinked': 'ফোন আনলিংক হয়েছে।',
  'qo.title': '{sender} — order mone hocche',
  'qo.sending': 'ড্রাফট বানানো হচ্ছে…',
  'qo.duplicate': 'মনে হচ্ছে এই অর্ডার আগেই আছে।',
  'qo.attention': 'এই কাস্টমারের আগের অর্ডার আছে; মেসেজটা Chat orders-এ আছে।',
  'qo.failed': 'ড্রাফট বানানো যায়নি।',
  'qo.retry': 'আবার চেষ্টা করুন',
  'qo.close': 'বন্ধ করুন',
  'qo.done': 'ঠিক আছে',
  'qo.expired':
      'এই চ্যাট আর ফোনে নেই (শেষ মেসেজের ২৫ মিনিট পর চ্যাট বন্ধ হয়ে যায়)।',
  'qo.nothing': 'দেখানোর মতো চ্যাট নেই।',
  'qo.signIn': 'আগে ecomsbd-এ সাইন ইন করুন।',
  'qo.openApp': 'ecomsbd খুলুন',
  'qo.messages': 'মেসেজ',
  'qo.checkMarked':
      'চিহ্নিত ঘরগুলো দেখে নিন: এগুলো পড়তে নিশ্চিত হওয়া যায়নি।',
  'qo.choosePhone': 'চ্যাটে একাধিক নম্বর আছে। ডেলিভারির নম্বরটা বেছে নিন।',
  'qo.phone': 'ফোন',
  'qo.name': 'কাস্টমারের নাম',
  'qo.address': 'ডেলিভারির ঠিকানা',
  'qo.items': 'পণ্য',
  'qo.item': 'পণ্য',
  'qo.qty': 'পরিমাণ',
  'qo.price': 'দাম (৳)',
  'qo.pick': 'পণ্য বেছে নিন',
  'qo.pickVariant': 'সাইজ বা রং বেছে নিন',
  'qo.notInCatalog': 'আপনার ক্যাটালগে নেই; যেমন লেখা তেমন সেভ হবে।',
  'qo.cod': 'যত টাকা COD নেবেন (৳)',
  'qo.codCatalog': 'আপনার দাম আর ডেলিভারি চার্জ থেকে হিসাব করা। দেখে নিন।',
  'qo.codMismatch':
      'চ্যাটে লেখা টাকা আপনার দাম আর ডেলিভারি চার্জের হিসাবের সাথে মেলে না। '
      'কাস্টমারের সাথে মিলিয়ে নিন।',
  'qo.zoneUnknown': 'এলাকা চেনা যায়নি: ডেলিভারি চার্জ যোগ হয়নি।',
  'qo.blocked': 'এই কাস্টমারকে আপনি ব্লক করেছেন।',
  'qo.knownCustomer': '{name} · আগে {count}টি অর্ডার',
  'qo.courierDefault': 'কুরিয়ার: {courier} (আপনার ডিফল্ট)',
  'qo.courierHistory':
      'কুরিয়ার: {courier} — এই এলাকায় {parcels}টির মধ্যে {delivered}টি ডেলিভারি '
      '(আপনার পার্সেল)',
  'qo.courierNote': 'বুকিং আলাদা: সেভ করলে কুরিয়ারে কিছু যায় না।',
  'qo.create': 'অর্ডার বানান',
  'qo.creating': 'বানানো হচ্ছে…',
  'qo.confirmCall': 'কনফার্ম করতে কাস্টমারকে কল করুন',
  'qo.confirmedInChat': 'চ্যাটেই কনফার্ম হয়েছে',
  'qo.confirmNote':
      'কল করে কনফার্ম: অর্ডার এগোনোর আগে কাস্টমারের কাছে যাচাই করা হবে। '
      'চ্যাটেই কনফার্ম: অর্ডারটা এখনই কনফার্ম হবে।',
  'qo.saveDraft': 'ড্রাফট সেভ করুন',
  'qo.saveDraftNote':
      'ড্রাফটটা Chat orders-এ থাকবে। এখানে করা বদলগুলো রাখা হয় না।',
  'qo.more': 'Aro message lagbe',
  'qo.moreTitle': 'আরও মেসেজ যোগ করুন',
  'qo.moreHint': 'চ্যাট থেকে মেসেজগুলো কপি করে এখানে পেস্ট করুন।',
  'qo.moreAdd': 'যোগ করুন',
  'qo.moreEmpty': 'আগে মেসেজগুলো পেস্ট করুন।',
  'qo.cancel': 'বাতিল',
  'qo.phoneRequired': 'ডেলিভারির ফোন নম্বর দিন',
  'qo.itemsRequired': 'অন্তত একটি পণ্য যোগ করুন।',
  'qo.chooseProduct': 'কাস্টমার কোন পণ্যটি চেয়েছেন তা বেছে নিন।',
  'qo.amountError': '১২৫০-এর মতো করে টাকা লিখুন',
  'qo.ordered': 'অর্ডার {number} বানানো হয়েছে।',
  'qo.orderedNoNumber': 'অর্ডার বানানো হয়েছে।',
  'qo.confirming': 'কাস্টমারের সাথে অর্ডারটা কনফার্ম করা হবে।',
  'qo.confirmedNow': 'চ্যাটে কাস্টমার কনফার্ম করেছেন বলে চিহ্নিত হয়েছে।',
  'qo.queued':
      'ইন্টারনেট নেই: অর্ডারটা এই ফোনে সেভ হয়েছে, অনলাইনে ফিরলে পাঠানো হবে।',
  'qo.closed': 'এই চ্যাট অর্ডারটা Chat orders-এ আগেই বন্ধ হয়েছে।',
};
