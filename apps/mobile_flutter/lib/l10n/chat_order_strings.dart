/// Order automation from Messenger and WhatsApp, and connecting stores and
/// chat channels natively on the phone.
///
/// Spread last into the catalogue, so the few keys it rewords (channel
/// descriptions, the Inbox's description, the old "on the web" notes) win.
library;

const chatOrderEn = <String, String>{
  // Inbox: chat orders.
  'inbox.description':
      'Orders from Messenger and WhatsApp chats, read for you to review and confirm.',
  'cho.sectionTitle': 'Orders from chat',
  'cho.sectionSub':
      'Customers keep chatting in Messenger or WhatsApp; each order is drafted here for you to check.',
  'cho.filterAll': 'All',
  'cho.filterReady': 'Ready ({count})',
  'cho.filterNeedsInfo': 'Needs info ({count})',
  'cho.connectFirst':
      'Connect Messenger or WhatsApp to turn customer messages into draft orders.',
  'cho.emptyTitle': 'No new orders from chat',
  'cho.emptyBody':
      'When a customer orders in Messenger or WhatsApp, the order appears here to review.',
  'cho.provider.MESSENGER': 'Messenger',
  'cho.provider.WHATSAPP': 'WhatsApp',
  'cho.newFrom': 'New order from {channel}',
  'cho.ready': 'Ready to review',
  'cho.needsInfo': 'Needs info',
  'cho.messages': '{count} messages',
  'cho.review': 'Review order',
  'cho.ignore': 'Ignore',
  'cho.ignored': 'Ignored. The chat stays in Messenger or WhatsApp.',
  'cho.phonesToChoose': '{count} numbers — choose one',
  'cho.codEstimate': '{amount} (from your prices)',
  'cho.missing': 'Missing: {fields}',
  'cho.field.phone': 'phone',
  'cho.field.address': 'address',
  'cho.field.items': 'product',
  'cho.warn.MULTIPLE_PHONES':
      'Several phone numbers — choose the delivery number.',
  'cho.warn.PRODUCT_AMBIGUOUS':
      'More than one product fits — choose which one the customer meant.',
  'cho.warn.PRODUCT_NOT_FOUND': 'A product is not in your catalogue.',
  'cho.warn.VARIANT_AMBIGUOUS': 'Choose the size or colour.',
  'cho.warn.CUSTOMER_AMBIGUOUS':
      'The numbers belong to different customers — check before confirming.',
  'cho.warn.CUSTOMER_BLOCKED': 'You blocked this customer.',
  'cho.warn.SESSION_FULL': 'A long chat — later messages were not read.',
  'cho.attachment': 'The customer sent an attachment — see it in {channel}.',
  'cho.customerAmbiguous':
      'This could be one of several customers. Check the phone number.',
  'cho.attentionTitle': 'Customers asking about an order',
  'cho.attentionSub': 'Nothing is changed automatically — reply in the chat.',
  'cho.intent.CANCEL_REQUEST': 'Possible cancellation request',
  'cho.intent.ADDRESS_CHANGE': 'Possible address change',
  'cho.intent.STATUS_QUESTION': 'Asking about delivery',
  'cho.attentionNote':
      'ecomsbd never cancels or changes an order from a message.',
  'cho.done': 'Done',
  // Review form.
  'cho.reviewTitle': 'Order from {channel}',
  'cho.reviewNote':
      'Read from the customer’s messages. Check every field; nothing is saved until you confirm.',
  'cho.confirmOrder': 'Confirm order',
  'cho.pickProduct': 'Choose product',
  'cho.suggested': 'Products that match',
  'cho.searchCatalogue': 'Search your products',
  'cho.noProducts': 'No product found.',
  'cho.variantCount': '{count} sizes/colours',
  'cho.choose': 'Choose',
  'cho.change': 'Change',
  'cho.productAmbiguous': 'Choose which product',
  'cho.variantNeeded': '{product}: choose size/colour',
  'cho.productNotFound': 'Not in catalogue — kept as written',
  'cho.customItem': 'Typed item',
  'cho.chooseFirst': 'Choose which product the customer meant.',
  // Notifications.
  'notif.cat.ORDERS': 'New orders from chat',
  'ns.cat.ORDERS':
      'When a Messenger or WhatsApp chat becomes an order to review.',

  // Connections: native setup.
  'conn.about.MESSENGER':
      'Catch orders automatically from your Facebook Page’s customer messages',
  'conn.about.WHATSAPP':
      'Pick up customer and order details from WhatsApp Business messages',
  'conn.connectNote.WOOCOMMERCE':
      'Connect with your store address — approve in WooCommerce or paste API keys.',
  'conn.connectNote.CUSTOM_WEBSITE':
      'Get an API key for your website to send orders to ecomsbd.',
  'conn.connectNote.SHOPIFY': 'Sign in to your Shopify store to connect.',
  'conn.connectNote.MESSENGER':
      'Sign in with Facebook and choose your Page. Orders from its messages arrive in the Inbox.',
  'conn.connectNote.WHATSAPP':
      'Add your WhatsApp Business number. Orders from its messages arrive in the Inbox.',
  'conn.reconnectNote': 'Open it and reconnect to keep orders coming in.',
  'int.sync.conflictsWeb':
      'Matched products sync on their own. Matching products by hand is not in the app yet.',
  'ics.connectTitle': 'Connect {provider}',
  'ics.sub.WOOCOMMERCE':
      'New WooCommerce orders arrive in ecomsbd automatically.',
  'ics.sub.SHOPIFY': 'New Shopify orders arrive in ecomsbd automatically.',
  'ics.sub.MESSENGER':
      'Customer messages to your Facebook Page become draft orders for you to confirm.',
  'ics.sub.WHATSAPP':
      'Connect your WhatsApp Business account. Customer messages arrive as draft orders in your ecomsbd Inbox.',
  'ics.sub.CUSTOM_WEBSITE':
      'Your website sends orders to ecomsbd with an API key.',
  'ics.storeUrl': 'Store address',
  'ics.storeUrlNote':
      'Your WooCommerce site’s public HTTPS address, such as https://mystore.com.',
  'ics.shopDomain': 'Shopify store',
  'ics.shopDomainNote': 'Your store’s myshopify.com address.',
  'ics.continue': 'Continue',
  'ics.manualInstead': 'Enter API keys instead',
  'ics.signInTitle': 'Sign in to {provider}',
  'ics.signInBody.MESSENGER':
      'Facebook opens in your browser. Allow ecomsbd for the Page you sell from, then come back here.',
  'ics.signInBody.WOOCOMMERCE':
      'Your store opens in your browser. Approve ecomsbd there, then come back here.',
  'ics.signInBody.SHOPIFY':
      'Shopify opens in your browser. Approve ecomsbd there, then come back here.',
  'ics.signInButton.MESSENGER': 'Continue with Facebook',
  'ics.signInButton.WOOCOMMERCE': 'Open my store',
  'ics.signInButton.SHOPIFY': 'Open Shopify',
  'ics.waitTitle': 'Finish in your browser',
  'ics.waitBody':
      'Approve ecomsbd in the browser. When you come back, ecomsbd checks the connection.',
  'ics.checkNow': 'I approved — check now',
  'ics.openAgain': 'Open the page again',
  'ics.notYet':
      'Not connected yet. Finish the approval in the browser, then check again.',
  'ics.declined': 'The approval was declined. Try again when you are ready.',
  'ics.noSignIn': 'This connection has no sign-in step.',
  'ics.browserError': 'Could not open a browser on this phone.',
  'ics.keysTitle': 'WooCommerce API keys',
  'ics.keysHelp':
      'In WordPress: WooCommerce → Settings → Advanced → REST API → Add key, with Read/Write permission. Paste both keys here.',
  'ics.saveKeys': 'Save and check keys',
  'ics.choosePage': 'Which Facebook Page do you sell from?',
  'ics.connected': 'Connected',
  'ics.chatNote':
      'Order automation: customer messages become draft orders in the Inbox. Replies stay in Messenger or WhatsApp.',
  'ics.continueSetup': 'Continue setup',
  'ics.disconnect': 'Disconnect',
  'ics.disconnectTitle': 'Disconnect?',
  'ics.disconnectBody':
      'New orders and messages stop coming in. Orders already in ecomsbd stay.',
  'ics.waHelp':
      'From Meta for Developers → WhatsApp → API setup: the Phone number ID, the WhatsApp Business Account ID and an access token for your own number.',
  'ics.waTitle': 'Connect WhatsApp',
  'ics.waDisconnectBody':
      'Disconnect WhatsApp from ecomsbd? Your WhatsApp Business account and phone number will remain with Meta.',
  'ics.waConnect': 'Connect with WhatsApp',
  'ics.waReconnect': 'Reconnect with WhatsApp',
  'ics.waRestart': 'Start WhatsApp signup again',
  'ics.waWaiting':
      'Complete Meta signup in your browser, then return here. We will check your connection.',
  'ics.waRefresh': 'Check connection',
  'int.code.META_APPROVAL_REQUIRED':
      'Meta approval is required before public WhatsApp signup is available.',
  'ics.waNumberId': 'Phone number ID',
  'ics.waWabaId': 'WhatsApp Business Account ID',
  'ics.waToken': 'Access token',
  'ics.siteName': 'Website name',
  'ics.nameRequired': 'Give the website a name.',
  'ics.createKey': 'Create API key',
  'ics.keyTitle': 'Your API key',
  'ics.signingSecret': 'Webhook signing secret',
  'ics.onceWarning': 'Shown only once',
  'ics.onceBody':
      'Copy it now and keep it safe. ecomsbd cannot show it again; if it is lost, rotate it for a new one.',
  'ics.copy': 'Copy',
  'ics.copied': 'Copied.',
  'ics.saved': 'I saved it',
  'ics.leaveTitle': 'Saved the key?',
  'ics.leaveBody': 'It will not be shown again.',
  'ics.leaveConfirm': 'Yes, continue',
  'ics.siteSetup': 'Website setup',
  'ics.apiBase': 'API base URL',
  'ics.ordersEndpoint': 'Send orders to (POST)',
  'ics.authHeader': 'Authentication header',
  'ics.authNote':
      'Send every request with your API key in this header, and a unique Idempotency-Key per order so a retry never makes two orders.',
  'ics.connectionId': 'Connection ID',
  'ics.checklist': 'Status',
  'ics.check.key': 'API key active',
  'ics.check.firstRequest': 'First API request · {when}',
  'ics.check.firstOrder': 'First order · {when}',
  'ics.check.live': 'Live',
  'ics.webhook': 'Webhook (order updates to your website)',
  'ics.webhookUrl': 'Webhook URL',
  'ics.topics': 'Events: {topics}',
  'ics.lastDelivery': 'Last delivery: {status} · {when}',
  'ics.webhookSave': 'Save webhook',
  'ics.webhookTest': 'Test webhook',
  'ics.webhookSent': 'Test event sent.',
  'ics.testOrder': 'Test order',
  'ics.testOrderOk': 'The test order is valid. Nothing was created.',
  'ics.testOrderBad': 'The test order has problems: {problems}',
  'ics.rotate': 'Rotate API key',
  'ics.rotateTitle': 'Rotate the API key?',
  'ics.rotateBody':
      'The current key stops working now. Update your website with the new one.',
  'ics.goLive': 'Go live',
  'int.code.NO_PAGES': 'No Facebook Page was shared with ecomsbd',
  'int.code.ACCESS_DENIED': 'The sign-in was cancelled or declined',
  'int.code.STATE_INVALID': 'The sign-in link expired — start again',
  'int.code.ACCOUNT_MISMATCH': 'A different store or Page than this connection',
  'int.code.ALREADY_LINKED': 'Already connected to another ecomsbd shop',
  'int.code.PAGE_UNKNOWN': 'Choose one of the Pages you allowed',
  'int.code.META_APP_SETUP_REQUIRED': 'ecomsbd’s Meta app is not set up yet',
  'int.code.SHOPIFY_APP_SETUP_REQUIRED':
      'ecomsbd’s Shopify app is not set up yet',
  'int.code.STORE_URL_INVALID': 'Enter the store’s public HTTPS address',
};

const chatOrderBn = <String, String>{
  'inbox.description':
      'Messenger ও WhatsApp চ্যাটের অর্ডার — আপনি দেখে নিশ্চিত করবেন।',
  'cho.sectionTitle': 'চ্যাট থেকে অর্ডার',
  'cho.sectionSub':
      'কাস্টমার Messenger বা WhatsApp-এই কথা বলবেন; প্রতিটি অর্ডার এখানে খসড়া হয়ে আসবে।',
  'cho.filterAll': 'সব',
  'cho.filterReady': 'প্রস্তুত ({count})',
  'cho.filterNeedsInfo': 'তথ্য বাকি ({count})',
  'cho.connectFirst':
      'কাস্টমারের মেসেজ থেকে অর্ডার পেতে Messenger বা WhatsApp যুক্ত করুন।',
  'cho.emptyTitle': 'চ্যাট থেকে নতুন অর্ডার নেই',
  'cho.emptyBody':
      'কাস্টমার Messenger বা WhatsApp-এ অর্ডার দিলে তা এখানে দেখে নিশ্চিত করতে পারবেন।',
  'cho.provider.MESSENGER': 'Messenger',
  'cho.provider.WHATSAPP': 'WhatsApp',
  'cho.newFrom': '{channel} থেকে নতুন অর্ডার',
  'cho.ready': 'দেখার জন্য প্রস্তুত',
  'cho.needsInfo': 'তথ্য বাকি',
  'cho.messages': '{count}টি মেসেজ',
  'cho.review': 'অর্ডার দেখুন',
  'cho.ignore': 'বাদ দিন',
  'cho.ignored': 'বাদ দেওয়া হয়েছে। চ্যাট Messenger বা WhatsApp-এই থাকবে।',
  'cho.phonesToChoose': '{count}টি নম্বর — একটি বেছে নিন',
  'cho.codEstimate': '{amount} (আপনার দাম অনুযায়ী)',
  'cho.missing': 'বাকি: {fields}',
  'cho.field.phone': 'ফোন',
  'cho.field.address': 'ঠিকানা',
  'cho.field.items': 'পণ্য',
  'cho.warn.MULTIPLE_PHONES': 'একাধিক ফোন নম্বর — ডেলিভারির নম্বর বেছে নিন।',
  'cho.warn.PRODUCT_AMBIGUOUS':
      'একাধিক পণ্যের সাথে মিলছে — কাস্টমার কোনটি চেয়েছেন বেছে নিন।',
  'cho.warn.PRODUCT_NOT_FOUND': 'একটি পণ্য আপনার ক্যাটালগে নেই।',
  'cho.warn.VARIANT_AMBIGUOUS': 'সাইজ বা রং বেছে নিন।',
  'cho.warn.CUSTOMER_AMBIGUOUS':
      'নম্বরগুলো ভিন্ন কাস্টমারের — নিশ্চিত করার আগে দেখে নিন।',
  'cho.warn.CUSTOMER_BLOCKED': 'এই কাস্টমারকে আপনি ব্লক করেছেন।',
  'cho.warn.SESSION_FULL': 'লম্বা চ্যাট — পরের মেসেজগুলো পড়া হয়নি।',
  'cho.attachment': 'কাস্টমার একটি ফাইল/ছবি পাঠিয়েছেন — {channel}-এ দেখুন।',
  'cho.customerAmbiguous':
      'এটি একাধিক কাস্টমারের একজন হতে পারেন। ফোন নম্বরটি দেখে নিন।',
  'cho.attentionTitle': 'অর্ডার নিয়ে কাস্টমারের প্রশ্ন',
  'cho.attentionSub': 'নিজে থেকে কিছু বদলানো হয় না — চ্যাটে উত্তর দিন।',
  'cho.intent.CANCEL_REQUEST': 'সম্ভবত অর্ডার বাতিলের অনুরোধ',
  'cho.intent.ADDRESS_CHANGE': 'সম্ভবত ঠিকানা পরিবর্তন',
  'cho.intent.STATUS_QUESTION': 'ডেলিভারি নিয়ে জানতে চাইছেন',
  'cho.attentionNote': 'মেসেজ দেখে ecomsbd কখনো অর্ডার বাতিল বা বদল করে না।',
  'cho.done': 'হয়েছে',
  'cho.reviewTitle': '{channel} থেকে অর্ডার',
  'cho.reviewNote':
      'কাস্টমারের মেসেজ থেকে পড়া। প্রতিটি ঘর দেখে নিন; নিশ্চিত না করা পর্যন্ত কিছু সেভ হয় না।',
  'cho.confirmOrder': 'অর্ডার নিশ্চিত করুন',
  'cho.pickProduct': 'পণ্য বেছে নিন',
  'cho.suggested': 'যেসব পণ্যের সাথে মিলছে',
  'cho.searchCatalogue': 'আপনার পণ্য খুঁজুন',
  'cho.noProducts': 'কোনো পণ্য পাওয়া যায়নি।',
  'cho.variantCount': '{count}টি সাইজ/রং',
  'cho.choose': 'বেছে নিন',
  'cho.change': 'বদলান',
  'cho.productAmbiguous': 'কোন পণ্য, বেছে নিন',
  'cho.variantNeeded': '{product}: সাইজ/রং বেছে নিন',
  'cho.productNotFound': 'ক্যাটালগে নেই — যেমন লেখা তেমন থাকবে',
  'cho.customItem': 'লিখে দেওয়া পণ্য',
  'cho.chooseFirst': 'কাস্টমার কোন পণ্যটি চেয়েছেন তা বেছে নিন।',
  'notif.cat.ORDERS': 'চ্যাট থেকে নতুন অর্ডার',
  'ns.cat.ORDERS': 'Messenger বা WhatsApp চ্যাট থেকে দেখার মতো অর্ডার এলে।',

  'conn.about.MESSENGER':
      'Facebook Page-এর customer message থেকে order automatically ধরুন',
  'conn.about.WHATSAPP':
      'WhatsApp Business message থেকে customer ও order information ধরুন',
  'conn.connectNote.WOOCOMMERCE':
      'স্টোরের ঠিকানা দিয়ে যুক্ত করুন — WooCommerce-এ অনুমোদন দিন বা API কী দিন।',
  'conn.connectNote.CUSTOM_WEBSITE':
      'ওয়েবসাইট থেকে ecomsbd-তে অর্ডার পাঠাতে একটি API কী নিন।',
  'conn.connectNote.SHOPIFY': 'যুক্ত করতে আপনার Shopify স্টোরে সাইন ইন করুন।',
  'conn.connectNote.MESSENGER':
      'Facebook দিয়ে সাইন ইন করে পেজ বেছে নিন। পেজের মেসেজ থেকে অর্ডার ইনবক্সে আসবে।',
  'conn.connectNote.WHATSAPP':
      'আপনার WhatsApp Business নম্বর যোগ করুন। মেসেজ থেকে অর্ডার ইনবক্সে আসবে।',
  'conn.reconnectNote': 'অর্ডার আসা চালু রাখতে খুলে আবার যুক্ত করুন।',
  'int.sync.conflictsWeb':
      'মেলানো পণ্য নিজে থেকেই সিঙ্ক হয়। হাতে পণ্য মেলানো এখনো অ্যাপে নেই।',
  'ics.connectTitle': '{provider} যুক্ত করুন',
  'ics.sub.WOOCOMMERCE':
      'WooCommerce-এর নতুন অর্ডার নিজে থেকেই ecomsbd-তে আসবে।',
  'ics.sub.SHOPIFY': 'Shopify-এর নতুন অর্ডার নিজে থেকেই ecomsbd-তে আসবে।',
  'ics.sub.MESSENGER':
      'আপনার Facebook Page-এ কাস্টমারের মেসেজ খসড়া অর্ডার হয়ে আসবে, আপনি নিশ্চিত করবেন।',
  'ics.sub.WHATSAPP':
      'আপনার WhatsApp Business অ্যাকাউন্ট যুক্ত করুন। Customer message থেকে order information ecomsbd Inbox-এ draft হিসেবে আসবে।',
  'ics.sub.CUSTOM_WEBSITE':
      'আপনার ওয়েবসাইট একটি API কী দিয়ে ecomsbd-তে অর্ডার পাঠাবে।',
  'ics.storeUrl': 'স্টোরের ঠিকানা',
  'ics.storeUrlNote':
      'আপনার WooCommerce সাইটের HTTPS ঠিকানা, যেমন https://mystore.com।',
  'ics.shopDomain': 'Shopify স্টোর',
  'ics.shopDomainNote': 'আপনার স্টোরের myshopify.com ঠিকানা।',
  'ics.continue': 'এগিয়ে যান',
  'ics.manualInstead': 'বরং API কী দিন',
  'ics.signInTitle': '{provider}-এ সাইন ইন করুন',
  'ics.signInBody.MESSENGER':
      'ব্রাউজারে Facebook খুলবে। যে পেজ থেকে বিক্রি করেন সেটির জন্য ecomsbd-কে অনুমতি দিয়ে এখানে ফিরে আসুন।',
  'ics.signInBody.WOOCOMMERCE':
      'ব্রাউজারে আপনার স্টোর খুলবে। সেখানে ecomsbd-কে অনুমোদন দিয়ে এখানে ফিরে আসুন।',
  'ics.signInBody.SHOPIFY':
      'ব্রাউজারে Shopify খুলবে। সেখানে ecomsbd-কে অনুমোদন দিয়ে এখানে ফিরে আসুন।',
  'ics.signInButton.MESSENGER': 'Facebook দিয়ে এগিয়ে যান',
  'ics.signInButton.WOOCOMMERCE': 'আমার স্টোর খুলুন',
  'ics.signInButton.SHOPIFY': 'Shopify খুলুন',
  'ics.waitTitle': 'ব্রাউজারে শেষ করুন',
  'ics.waitBody':
      'ব্রাউজারে ecomsbd-কে অনুমোদন দিন। ফিরে এলে ecomsbd সংযোগটি যাচাই করবে।',
  'ics.checkNow': 'অনুমোদন দিয়েছি — এখন দেখুন',
  'ics.openAgain': 'পেজটি আবার খুলুন',
  'ics.notYet': 'এখনো যুক্ত হয়নি। ব্রাউজারে অনুমোদন শেষ করে আবার দেখুন।',
  'ics.declined': 'অনুমোদন দেওয়া হয়নি। প্রস্তুত হলে আবার চেষ্টা করুন।',
  'ics.noSignIn': 'এই সংযোগে সাইন ইন ধাপ নেই।',
  'ics.browserError': 'এই ফোনে ব্রাউজার খোলা যায়নি।',
  'ics.keysTitle': 'WooCommerce API কী',
  'ics.keysHelp':
      'WordPress-এ: WooCommerce → Settings → Advanced → REST API → Add key, Read/Write অনুমতিসহ। দুটি কী এখানে দিন।',
  'ics.saveKeys': 'কী সেভ করে যাচাই করুন',
  'ics.choosePage': 'কোন Facebook Page থেকে বিক্রি করেন?',
  'ics.connected': 'যুক্ত হয়েছে',
  'ics.chatNote':
      'অর্ডার অটোমেশন: কাস্টমারের মেসেজ ইনবক্সে খসড়া অর্ডার হয়ে আসে। উত্তর Messenger বা WhatsApp-এই দিন।',
  'ics.continueSetup': 'সেটআপ শেষ করুন',
  'ics.disconnect': 'সংযোগ বিচ্ছিন্ন করুন',
  'ics.disconnectTitle': 'সংযোগ বিচ্ছিন্ন করবেন?',
  'ics.disconnectBody':
      'নতুন অর্ডার ও মেসেজ আসা বন্ধ হবে। আগের অর্ডার ecomsbd-তেই থাকবে।',
  'ics.waHelp':
      'Meta for Developers → WhatsApp → API setup থেকে: আপনার নম্বরের Phone number ID, WhatsApp Business Account ID ও একটি access token।',
  'ics.waTitle': 'WhatsApp যুক্ত করুন',
  'ics.waDisconnectBody':
      'ecomsbd থেকে WhatsApp সংযোগ বিচ্ছিন্ন করবেন? আপনার WhatsApp Business অ্যাকাউন্ট ও ফোন নম্বর Meta-তে থাকবে।',
  'ics.waConnect': 'WhatsApp দিয়ে যুক্ত করুন',
  'ics.waReconnect': 'WhatsApp দিয়ে আবার যুক্ত করুন',
  'ics.waRestart': 'WhatsApp সংযোগ আবার শুরু করুন',
  'ics.waWaiting':
      'ব্রাউজারে Meta-এর সংযোগ সম্পূর্ণ করে এখানে ফিরে আসুন। সংযোগ যাচাই করা হবে।',
  'ics.waRefresh': 'সংযোগ যাচাই করুন',
  'int.code.META_APPROVAL_REQUIRED':
      'সবার জন্য WhatsApp সংযোগ চালু করতে Meta-এর অনুমোদন প্রয়োজন।',
  'ics.waNumberId': 'Phone number ID',
  'ics.waWabaId': 'WhatsApp Business Account ID',
  'ics.waToken': 'Access token',
  'ics.siteName': 'ওয়েবসাইটের নাম',
  'ics.nameRequired': 'ওয়েবসাইটের একটি নাম দিন।',
  'ics.createKey': 'API কী তৈরি করুন',
  'ics.keyTitle': 'আপনার API কী',
  'ics.signingSecret': 'ওয়েবহুক সাইনিং সিক্রেট',
  'ics.onceWarning': 'শুধু একবার দেখানো হবে',
  'ics.onceBody':
      'এখনই কপি করে নিরাপদে রাখুন। ecomsbd এটি আর দেখাতে পারবে না; হারালে নতুন কী নিন।',
  'ics.copy': 'কপি',
  'ics.copied': 'কপি হয়েছে।',
  'ics.saved': 'সেভ করেছি',
  'ics.leaveTitle': 'কী সেভ করেছেন?',
  'ics.leaveBody': 'এটি আর দেখানো হবে না।',
  'ics.leaveConfirm': 'হ্যাঁ, এগিয়ে যান',
  'ics.siteSetup': 'ওয়েবসাইট সেটআপ',
  'ics.apiBase': 'API base URL',
  'ics.ordersEndpoint': 'অর্ডার পাঠাবেন (POST)',
  'ics.authHeader': 'Authentication header',
  'ics.authNote':
      'প্রতিটি রিকোয়েস্টে এই হেডারে API কী দিন, আর প্রতি অর্ডারে আলাদা Idempotency-Key দিন — আবার পাঠালেও দুটি অর্ডার হবে না।',
  'ics.connectionId': 'Connection ID',
  'ics.checklist': 'অবস্থা',
  'ics.check.key': 'API কী চালু',
  'ics.check.firstRequest': 'প্রথম API রিকোয়েস্ট · {when}',
  'ics.check.firstOrder': 'প্রথম অর্ডার · {when}',
  'ics.check.live': 'লাইভ',
  'ics.webhook': 'ওয়েবহুক (আপনার ওয়েবসাইটে অর্ডারের আপডেট)',
  'ics.webhookUrl': 'Webhook URL',
  'ics.topics': 'ইভেন্ট: {topics}',
  'ics.lastDelivery': 'শেষ পাঠানো: {status} · {when}',
  'ics.webhookSave': 'ওয়েবহুক সেভ করুন',
  'ics.webhookTest': 'ওয়েবহুক টেস্ট',
  'ics.webhookSent': 'টেস্ট ইভেন্ট পাঠানো হয়েছে।',
  'ics.testOrder': 'টেস্ট অর্ডার',
  'ics.testOrderOk': 'টেস্ট অর্ডার ঠিক আছে। কিছু তৈরি হয়নি।',
  'ics.testOrderBad': 'টেস্ট অর্ডারে সমস্যা: {problems}',
  'ics.rotate': 'নতুন API কী',
  'ics.rotateTitle': 'নতুন API কী নেবেন?',
  'ics.rotateBody':
      'বর্তমান কী এখনই বন্ধ হবে। নতুন কী দিয়ে ওয়েবসাইট আপডেট করুন।',
  'ics.goLive': 'লাইভ করুন',
  'int.code.NO_PAGES': 'ecomsbd-কে কোনো Facebook Page দেওয়া হয়নি',
  'int.code.ACCESS_DENIED': 'সাইন ইন বাতিল বা প্রত্যাখ্যান করা হয়েছে',
  'int.code.STATE_INVALID': 'সাইন ইনের লিংকের মেয়াদ শেষ — আবার শুরু করুন',
  'int.code.ACCOUNT_MISMATCH': 'এই সংযোগের চেয়ে আলাদা স্টোর বা পেজ',
  'int.code.ALREADY_LINKED': 'অন্য একটি ecomsbd শপের সাথে আগেই যুক্ত',
  'int.code.PAGE_UNKNOWN': 'আপনি অনুমতি দেওয়া পেজগুলোর একটি বেছে নিন',
  'int.code.META_APP_SETUP_REQUIRED': 'ecomsbd-এর Meta অ্যাপ এখনো সেটআপ হয়নি',
  'int.code.SHOPIFY_APP_SETUP_REQUIRED':
      'ecomsbd-এর Shopify অ্যাপ এখনো সেটআপ হয়নি',
  'int.code.STORE_URL_INVALID': 'স্টোরের HTTPS ঠিকানা দিন',
};
