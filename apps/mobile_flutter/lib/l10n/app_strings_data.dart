/// The translation catalogue.
///
/// Shared keys, two languages, one entry per string. Split into per-area maps
/// so a screen's copy is readable in one place rather than scattered through
/// one enormous literal.
///
/// Bangla is written the way a Bangladeshi seller speaks about their own
/// business — "কুরিয়ারের কাছে কত টাকা", not a literal rendering of the English.
/// Where a term is genuinely used in English on the ground (COD, API, CSV,
/// courier brand names) it stays in English inside the Bangla string, because
/// translating it would make the sentence harder to read, not easier.
///
/// Placeholders are `{name}` and are substituted by `AppStrings.t`.
library;

final Map<String, String> banglaStrings = <String, String>{
  ..._commonBn,
  ..._authBn,
  ..._onboardingBn,
  ..._homeBn,
  ..._ordersBn,
  ..._moneyBn,
  ..._insightsBn,
  ..._menuBn,
  ..._notifBn,
  ..._settingsBn,
  ..._otpBn,
  ..._fieldsBn,
  ..._authErrBn,
  ..._componentsBn,
};

final Map<String, String> englishStrings = <String, String>{
  ..._commonEn,
  ..._authEn,
  ..._onboardingEn,
  ..._homeEn,
  ..._ordersEn,
  ..._moneyEn,
  ..._insightsEn,
  ..._menuEn,
  ..._notifEn,
  ..._settingsEn,
  ..._otpEn,
  ..._fieldsEn,
  ..._authErrEn,
  ..._componentsEn,
};

// --------------------------------------------------------------------------- //
// Common: buttons, states, relative time, shared notices
// --------------------------------------------------------------------------- //

const Map<String, String> _commonEn = <String, String>{
  'common.tryAgain': 'Try again',
  'common.retry': 'Retry',
  'common.cancel': 'Cancel',
  'common.close': 'Close',
  'common.back': 'Back',
  'common.open': 'Open',
  'common.loadMore': 'Load more',
  'common.signOut': 'Sign out',
  'common.loading': 'Loading…',
  'common.optional': 'Optional',
  'common.yourShop': 'Your shop',
  'common.customer': 'Customer',
  'common.couldNotLoad': 'Could not load',
  'common.couldNotLoadThis': 'Could not load this.',
  'common.noConnectionTitle': 'No connection',
  'common.offline': 'Offline',
  'common.upToDate': 'Up to date',
  'common.markRead': 'Mark read',
  'common.notEnoughData': 'Not enough data',
  'common.notEnoughDataYet': 'Not enough data yet',
  'common.noRankYet': 'No rank yet',
  'common.notSavedOnDevice': 'Not saved on this device yet.',
  'common.unknownTime': 'an unknown time',
  'common.justNow': 'just now',
  'common.minutesAgo.one': '{count} minute ago',
  'common.minutesAgo.other': '{count} minutes ago',
  'common.hoursAgo.one': '{count} hour ago',
  'common.hoursAgo.other': '{count} hours ago',
  'common.daysAgo.one': '{count} day ago',
  'common.daysAgo.other': '{count} days ago',

  'language.title': 'Language',
  'language.subtitle': 'Bangla or English — applies across the whole app',

  'nav.home': 'Home',
  'nav.orders': 'Orders',
  'nav.money': 'Money',
  'nav.insights': 'Insights',
  'nav.menu': 'Menu',
  'nav.newOrderTooltip': 'New order',
  'nav.searchTooltip': 'Search ecomsbd',
  'nav.notificationsTooltip': 'Notifications',
  'nav.unreadTooltip': '{count} unread',

  'severity.critical': 'Losing money',
  'severity.warning': 'At risk',
  'severity.action': 'Needs you',
  'severity.info': 'For info',

  'sync.notSynced': 'Not synced',
  'sync.sending': 'Sending',
  'sync.needsChoice': 'Needs a choice',
  'sync.needsFixing': 'Needs fixing',

  'stale.notice':
      'Saved data from {when}. It will refresh when you are back online.',

  'offline.detail.one':
      '{count} change will sync when you are back online. '
      'Courier booking needs a connection.',
  'offline.detail.other':
      '{count} changes will sync when you are back online. '
      'Courier booking needs a connection.',
  'offline.detailPlain':
      'You can still create and edit orders. Courier booking needs a '
      'connection.',
  'error.offlineListBody':
      'Nothing is saved on this device for this list yet. You can still create '
      'and edit — it will sync when you are online.',

  'plan.lockedTitle': 'Not included in your plan',
  'plan.lockedBody':
      'Your plan shows today’s figures. Upgrade for history and breakdowns.',
  'plan.seePlans': 'See plans',

  'weekday.mon': 'Mon',
  'weekday.tue': 'Tue',
  'weekday.wed': 'Wed',
  'weekday.thu': 'Thu',
  'weekday.fri': 'Fri',
  'weekday.sat': 'Sat',
  'weekday.sun': 'Sun',

  'month.1': 'Jan',
  'month.2': 'Feb',
  'month.3': 'Mar',
  'month.4': 'Apr',
  'month.5': 'May',
  'month.6': 'Jun',
  'month.7': 'Jul',
  'month.8': 'Aug',
  'month.9': 'Sep',
  'month.10': 'Oct',
  'month.11': 'Nov',
  'month.12': 'Dec',
};

const Map<String, String> _commonBn = <String, String>{
  'common.tryAgain': 'আবার চেষ্টা করুন',
  'common.retry': 'আবার',
  'common.cancel': 'বাতিল',
  'common.close': 'বন্ধ করুন',
  'common.back': 'পেছনে',
  'common.open': 'খুলুন',
  'common.loadMore': 'আরও দেখুন',
  'common.signOut': 'সাইন আউট',
  'common.loading': 'লোড হচ্ছে…',
  'common.optional': 'ঐচ্ছিক',
  'common.yourShop': 'আপনার শপ',
  'common.customer': 'কাস্টমার',
  'common.couldNotLoad': 'লোড করা যায়নি',
  'common.couldNotLoadThis': 'এটি লোড করা যায়নি।',
  'common.noConnectionTitle': 'ইন্টারনেট সংযোগ নেই',
  'common.offline': 'অফলাইন',
  'common.upToDate': 'সব আপডেট আছে',
  'common.markRead': 'পড়া হয়েছে',
  'common.notEnoughData': 'যথেষ্ট তথ্য নেই',
  'common.notEnoughDataYet': 'এখনও যথেষ্ট তথ্য নেই',
  'common.noRankYet': 'এখনও র‍্যাঙ্ক নেই',
  'common.notSavedOnDevice': 'এই ফোনে এখনও সেভ হয়নি।',
  'common.unknownTime': 'অজানা সময়',
  'common.justNow': 'এইমাত্র',
  'common.minutesAgo.one': '{count} মিনিট আগে',
  'common.minutesAgo.other': '{count} মিনিট আগে',
  'common.hoursAgo.one': '{count} ঘণ্টা আগে',
  'common.hoursAgo.other': '{count} ঘণ্টা আগে',
  'common.daysAgo.one': '{count} দিন আগে',
  'common.daysAgo.other': '{count} দিন আগে',

  'language.title': 'ভাষা',
  'language.subtitle': 'বাংলা বা ইংরেজি — পুরো অ্যাপেই কাজ করবে',

  'nav.home': 'হোম',
  'nav.orders': 'অর্ডার',
  'nav.money': 'টাকা',
  'nav.insights': 'বিশ্লেষণ',
  'nav.menu': 'মেনু',
  'nav.newOrderTooltip': 'নতুন অর্ডার',
  'nav.searchTooltip': 'ecomsbd-এ খুঁজুন',
  'nav.notificationsTooltip': 'নোটিফিকেশন',
  'nav.unreadTooltip': '{count}টি অপঠিত',

  'severity.critical': 'টাকা নষ্ট হচ্ছে',
  'severity.warning': 'ঝুঁকিতে আছে',
  'severity.action': 'আপনাকে দেখতে হবে',
  'severity.info': 'জানার জন্য',

  'sync.notSynced': 'সিঙ্ক হয়নি',
  'sync.sending': 'পাঠানো হচ্ছে',
  'sync.needsChoice': 'সিদ্ধান্ত দরকার',
  'sync.needsFixing': 'ঠিক করতে হবে',

  'stale.notice': '{when} সেভ করা তথ্য। অনলাইনে ফিরলে নিজেই আপডেট হয়ে যাবে।',

  'offline.detail.one':
      '{count}টি পরিবর্তন অনলাইনে ফিরলে সিঙ্ক হবে। '
      'কুরিয়ার বুকিংয়ের জন্য ইন্টারনেট লাগবে।',
  'offline.detail.other':
      '{count}টি পরিবর্তন অনলাইনে ফিরলে সিঙ্ক হবে। '
      'কুরিয়ার বুকিংয়ের জন্য ইন্টারনেট লাগবে।',
  'offline.detailPlain':
      'অর্ডার তৈরি আর এডিট এখনও করতে পারবেন। কুরিয়ার বুকিংয়ের জন্য '
      'ইন্টারনেট লাগবে।',
  'error.offlineListBody':
      'এই তালিকার কিছুই এখনও এই ফোনে সেভ নেই। তৈরি আর এডিট এখনও করতে '
      'পারবেন — অনলাইনে গেলে সিঙ্ক হয়ে যাবে।',

  'plan.lockedTitle': 'আপনার প্ল্যানে এটি নেই',
  'plan.lockedBody':
      'আপনার প্ল্যানে শুধু আজকের হিসাব দেখা যায়। পুরোনো হিসাব আর বিস্তারিত '
      'ভাগের জন্য আপগ্রেড করুন।',
  'plan.seePlans': 'প্ল্যান দেখুন',

  'weekday.mon': 'সোম',
  'weekday.tue': 'মঙ্গল',
  'weekday.wed': 'বুধ',
  'weekday.thu': 'বৃহ',
  'weekday.fri': 'শুক্র',
  'weekday.sat': 'শনি',
  'weekday.sun': 'রবি',

  'month.1': 'জানু',
  'month.2': 'ফেব',
  'month.3': 'মার্চ',
  'month.4': 'এপ্রি',
  'month.5': 'মে',
  'month.6': 'জুন',
  'month.7': 'জুলা',
  'month.8': 'আগ',
  'month.9': 'সেপ্ট',
  'month.10': 'অক্টো',
  'month.11': 'নভে',
  'month.12': 'ডিসে',
};

// --------------------------------------------------------------------------- //
// Auth
// --------------------------------------------------------------------------- //

const Map<String, String> _authEn = <String, String>{
  'auth.welcomeBack': 'Welcome back',
  'auth.createAccount': 'Create account',
  'auth.forgotPassword': 'Forgot password',
  'auth.resetPassword': 'Reset password',
  'auth.checkYourEmail': 'Check your email',
  'auth.passwordUpdated': 'Password updated',
  'auth.resetSentBody':
      'If an account uses this email, you’ll receive a password reset link. '
      'Open it to choose a new password, then return here to sign in.',
  'auth.resetDoneBody':
      'Your password has been reset. Sign in with your new password.',
  'auth.resetLinkIncomplete':
      'This reset link is incomplete. Request a new one.',
  'auth.backToSignIn': 'Back to Sign In',
  'auth.continueWithGoogle': 'Continue with Google',
  'auth.continueWithApple': 'Continue with Apple',
  'auth.orSignInWithEmail': 'or sign in with email',
  'auth.forgotIntro':
      'Enter your account email to request a password reset link.',
  'auth.email': 'Email',
  'auth.emailInvalid': 'Enter a valid email address.',
  'auth.password': 'Password',
  'auth.newPassword': 'New password',
  'auth.confirmPassword': 'Confirm password',
  'auth.passwordRule': 'Use 10–200 characters.',
  'auth.enterPassword': 'Enter your password.',
  'auth.passwordsMismatch': 'Passwords do not match.',
  'auth.showPassword': 'Show password',
  'auth.hidePassword': 'Hide password',
  'auth.signIn': 'Sign In',
  'auth.sendResetLink': 'Send reset link',
  'auth.signInWithPhone': 'Sign in with phone',

  'auth.verifyTitle': 'Verify your email',
  'auth.verifiedTitle': 'Email verified',
  'auth.verifiedBody':
      'Your email address is verified. You can continue to ecomsbd.',
  'auth.verifyButton': 'Verify email',
  'auth.verifyCheckBody':
      'Check {email} for the confirmation link. Check your spam folder too. '
      'If it does not arrive, try resending.',
  'auth.yourEmailAddress': 'your email address',
  'auth.resendVerification': 'Resend verification email',
  'auth.resendIn': 'Resend in {count} seconds',
  'auth.resentNote':
      'If verification is still pending, a new link will arrive in your inbox.',
  'auth.continueSetupNote':
      'You can continue setting up your shop while you verify your email.',
  'auth.continueToShop': 'Continue to shop',
  'auth.chooseShop': 'Choose your shop',

  'auth.phoneHeadline': 'All your COD money\nin one place',
  'auth.phoneSubhead':
      'How much the courier is holding, what never matched, and what each '
      'order actually earned — in one app.',
  'auth.mobileNumber': 'Mobile number',
  'auth.banglaDigitsOk': 'Bangla digits work too — ০১৭১২৩৪৫৬৭৮',
  'auth.sendCode': 'Send code',
  'auth.phoneFooter':
      'Your number is used to send the code. No password needed.',
  'auth.phoneRequired': 'Enter your mobile number',
  'auth.phoneInvalid':
      'Enter a valid Bangladeshi number. For example: 01712345678',
};

const Map<String, String> _authBn = <String, String>{
  'auth.welcomeBack': 'আবার স্বাগতম',
  'auth.createAccount': 'অ্যাকাউন্ট তৈরি করুন',
  'auth.forgotPassword': 'পাসওয়ার্ড ভুলে গেছেন',
  'auth.resetPassword': 'পাসওয়ার্ড রিসেট করুন',
  'auth.checkYourEmail': 'ইমেইল দেখুন',
  'auth.passwordUpdated': 'পাসওয়ার্ড বদলে গেছে',
  'auth.resetSentBody':
      'এই ইমেইলে কোনো অ্যাকাউন্ট থাকলে পাসওয়ার্ড রিসেটের লিংক পাবেন। '
      'লিংকে গিয়ে নতুন পাসওয়ার্ড দিন, তারপর এখানে এসে সাইন ইন করুন।',
  'auth.resetDoneBody':
      'আপনার পাসওয়ার্ড রিসেট হয়েছে। নতুন পাসওয়ার্ড দিয়ে সাইন ইন করুন।',
  'auth.resetLinkIncomplete': 'এই রিসেট লিংকটি পুরো নয়। নতুন একটি লিংক নিন।',
  'auth.backToSignIn': 'সাইন ইনে ফিরুন',
  'auth.continueWithGoogle': 'Google দিয়ে চালিয়ে যান',
  'auth.continueWithApple': 'Apple দিয়ে চালিয়ে যান',
  'auth.orSignInWithEmail': 'অথবা ইমেইল দিয়ে সাইন ইন করুন',
  'auth.forgotIntro': 'রিসেট লিংক পেতে আপনার অ্যাকাউন্টের ইমেইল দিন।',
  'auth.email': 'ইমেইল',
  'auth.emailInvalid': 'সঠিক ইমেইল দিন।',
  'auth.password': 'পাসওয়ার্ড',
  'auth.newPassword': 'নতুন পাসওয়ার্ড',
  'auth.confirmPassword': 'পাসওয়ার্ড আবার দিন',
  'auth.passwordRule': '১০–২০০ অক্ষরের মধ্যে দিন।',
  'auth.enterPassword': 'পাসওয়ার্ড দিন।',
  'auth.passwordsMismatch': 'দুটি পাসওয়ার্ড মিলছে না।',
  'auth.showPassword': 'পাসওয়ার্ড দেখান',
  'auth.hidePassword': 'পাসওয়ার্ড লুকান',
  'auth.signIn': 'সাইন ইন',
  'auth.sendResetLink': 'রিসেট লিংক পাঠান',
  'auth.signInWithPhone': 'ফোন নম্বর দিয়ে সাইন ইন',

  'auth.verifyTitle': 'ইমেইল ভেরিফাই করুন',
  'auth.verifiedTitle': 'ইমেইল ভেরিফাই হয়েছে',
  'auth.verifiedBody':
      'আপনার ইমেইল ভেরিফাই হয়েছে। এখন ecomsbd-তে চালিয়ে যেতে পারেন।',
  'auth.verifyButton': 'ইমেইল ভেরিফাই করুন',
  'auth.verifyCheckBody':
      '{email}-এ পাঠানো কনফার্মেশন লিংকটি দেখুন। স্প্যাম ফোল্ডারও দেখে নিন। '
      'না এলে আবার পাঠাতে পারেন।',
  'auth.yourEmailAddress': 'আপনার ইমেইল',
  'auth.resendVerification': 'আবার ভেরিফিকেশন ইমেইল পাঠান',
  'auth.resendIn': '{count} সেকেন্ড পরে আবার পাঠান',
  'auth.resentNote':
      'ভেরিফিকেশন এখনও বাকি থাকলে নতুন একটি লিংক আপনার ইনবক্সে যাবে।',
  'auth.continueSetupNote':
      'ইমেইল ভেরিফাই করার ফাঁকেই শপ সেটআপ চালিয়ে যেতে পারেন।',
  'auth.continueToShop': 'শপে চলুন',
  'auth.chooseShop': 'আপনার শপ বেছে নিন',

  'auth.phoneHeadline': 'আপনার COD-এর টাকা\nএক জায়গায়',
  'auth.phoneSubhead':
      'কুরিয়ারের কাছে কত টাকা আছে, কোনটা মেলেনি, আর প্রতিটি অর্ডারে '
      'আসলে কত লাভ — সব এক অ্যাপে।',
  'auth.mobileNumber': 'মোবাইল নম্বর',
  'auth.banglaDigitsOk': 'বাংলা সংখ্যাও চলবে — ০১৭১২৩৪৫৬৭৮',
  'auth.sendCode': 'কোড পাঠান',
  'auth.phoneFooter':
      'কোড পাঠাতে আপনার নম্বর ব্যবহার করা হবে। কোনো পাসওয়ার্ড লাগবে না।',
  'auth.phoneRequired': 'মোবাইল নম্বর দিন',
  'auth.phoneInvalid': 'সঠিক বাংলাদেশি নম্বর দিন। যেমন: 01712345678',
};

// --------------------------------------------------------------------------- //
// Onboarding / create shop
// --------------------------------------------------------------------------- //

const Map<String, String> _onboardingEn = <String, String>{
  'onboarding.title': 'Create your shop',
  'onboarding.subtitle':
      'The name is enough to get started. You can add the rest later.',
  'onboarding.shopName': 'Shop name',
  'onboarding.shopNameRequired': 'Enter your shop name',
  'onboarding.businessType': 'Business type',
  'onboarding.pickupAddress': 'Pickup address',
  'onboarding.district': 'District',
  'onboarding.area': 'Area',
  'onboarding.pickupPhone': 'Pickup contact number',
  'onboarding.createShop': 'Create shop',
  'onboarding.manualTitle': 'You do not have to add a courier yet',
  'onboarding.manualBody':
      'Orders, tracking and payout statements can all be run manually. Add the '
      'API whenever you are ready.',
  'onboarding.manualMode': 'Manual mode',
  'onboarding.category.CLOTHING': 'Clothing / fashion',
  'onboarding.category.ELECTRONICS': 'Electronics',
  'onboarding.category.COSMETICS': 'Cosmetics',
  'onboarding.category.FOOD': 'Food',
  'onboarding.category.HOME': 'Home / kitchen',
  'onboarding.category.JEWELLERY': 'Jewellery',
  'onboarding.category.BABY': 'Baby',
  'onboarding.category.BOOKS': 'Books',
  'onboarding.category.OTHER': 'Other',
};

const Map<String, String> _onboardingBn = <String, String>{
  'onboarding.title': 'আপনার শপ তৈরি করুন',
  'onboarding.subtitle':
      'শুধু নামটা দিলেই শুরু করা যাবে। বাকি তথ্য পরেও যোগ করতে পারবেন।',
  'onboarding.shopName': 'শপের নাম',
  'onboarding.shopNameRequired': 'শপের নাম দিন',
  'onboarding.businessType': 'ব্যবসার ধরন',
  'onboarding.pickupAddress': 'পিকআপ ঠিকানা',
  'onboarding.district': 'জেলা',
  'onboarding.area': 'এলাকা',
  'onboarding.pickupPhone': 'পিকআপ যোগাযোগ নম্বর',
  'onboarding.createShop': 'শপ তৈরি করুন',
  'onboarding.manualTitle': 'কুরিয়ার এখন যোগ করতে হবে না',
  'onboarding.manualBody':
      'অর্ডার, ট্র্যাকিং আর পেআউট স্টেটমেন্ট দিয়ে পুরো হিসাব ম্যানুয়ালি '
      'চালানো যাবে। API পরে যুক্ত করলেই হবে।',
  'onboarding.manualMode': 'ম্যানুয়াল মোড',
  'onboarding.category.CLOTHING': 'কাপড় / ফ্যাশন',
  'onboarding.category.ELECTRONICS': 'ইলেকট্রনিকস',
  'onboarding.category.COSMETICS': 'কসমেটিকস',
  'onboarding.category.FOOD': 'খাবার',
  'onboarding.category.HOME': 'হোম / কিচেন',
  'onboarding.category.JEWELLERY': 'জুয়েলারি',
  'onboarding.category.BABY': 'বেবি',
  'onboarding.category.BOOKS': 'বই',
  'onboarding.category.OTHER': 'অন্যান্য',
};

// --------------------------------------------------------------------------- //
// Home
// --------------------------------------------------------------------------- //

const Map<String, String> _homeEn = <String, String>{
  'home.loadingToday': 'Loading today',
  'home.heroSubtitle.one': '{count} order today · {delivered} delivered',
  'home.heroSubtitle.other': '{count} orders today · {delivered} delivered',
  'home.chipOverdue': '{amount} overdue',
  'home.chipMismatch.one': '{count} mismatch',
  'home.chipMismatch.other': '{count} mismatches',
  'home.chipReturned': '{count} returned today',

  'home.codEyebrow': 'COD outstanding · with couriers right now',
  'home.codNothing': 'Nothing with couriers.',
  'home.codExpectedToday':
      '{amount} expected today, from your own settlement history.',
  'home.codNoDate':
      'No arrival date yet — not enough settlement history with these couriers '
      'to say when it lands.',
  'home.codNothingToday': 'Nothing expected to land today.',
  'home.realizedToday': 'Realized today',
  'home.deliveredCaption': '{count} delivered',
  'home.profitToday': 'Profit today',
  'home.contribution': 'contribution',
  'home.mismatch': 'Mismatch',
  'home.openCaption': '{count} open',

  'home.quick.newOrder': 'New order',
  'home.quick.newOrderSub': 'Paste or manual',
  'home.quick.riskCheck': 'Risk check',
  'home.quick.riskCheckSub': 'Phone history',
  'home.quick.addPayout': 'Add payout',
  'home.quick.addPayoutSub': 'API · CSV · manual',
  'home.quick.products': 'Products',
  'home.quick.productsSub': 'Cost · stock · margin',

  'home.reviewAll': 'Review all',
  'home.todaysContribution': 'Today’s contribution profit',
  'home.openProfitBreakdown': 'Open profit breakdown',
  'home.businessPulse': 'Business pulse',
  'home.businessPulseSub': '30-day profit, COD and delivery quality',
  'home.fullAnalytics': 'Full analytics',
  'home.liveActivity': 'Live activity',
  'home.liveActivitySub': 'Money and operational events only',
  'home.allActivity': 'All activity',
  'home.deliveredRate': '{rate}% delivered',

  'chart.contributionProfit': 'Contribution profit',
  'chart.contributionProfitSub':
      'Last 30 days · estimated inputs marked, not blended',
  'chart.codPosition': 'COD position',
  'chart.codPositionSub': 'Current collectible pipeline, by age',
  'chart.deliveryFunnel': 'Delivery funnel',
  'chart.last30': 'Last 30 days',
  'chart.courierHealth': 'Courier health',
  'chart.courierHealthSub': 'Your own history only · sample-aware',
  'chart.topProducts': 'Top products by profit',
  'chart.topProductsSub': 'Not revenue · contribution profit',
  'chart.returnPressure': 'Return pressure',
  'chart.returnPressureSub': 'Where returns actually cost money',
  'chart.emptyNoSettled': 'No settled parcels in the last 30 days yet.',
  'chart.emptyNoCod': 'No money with couriers right now.',
  'chart.emptyNoDispatched': 'No parcels dispatched in the last 30 days.',
  'chart.emptyNoCourierSample':
      'No finished parcels to judge a courier on yet.',
  'chart.emptyNoDelivered': 'No delivered products in the last 30 days.',
  'chart.emptyNoReturns': 'No returns in the last 30 days.',

  'courier.finishedReturned': '{finished} finished · {returned} returned',
  'courier.backRate': '{rate} back',

  'activity.nothingTitle': 'Nothing needs you right now',
  'activity.nothingBody':
      'Alerts appear here when money is at risk — delivered parcels that were '
      'never paid for, payments that came up short, parcels stuck with a '
      'courier.',
};

const Map<String, String> _homeBn = <String, String>{
  'home.loadingToday': 'আজকের হিসাব আসছে',
  'home.heroSubtitle.one': 'আজ {count}টি অর্ডার · {delivered}টি ডেলিভারি',
  'home.heroSubtitle.other': 'আজ {count}টি অর্ডার · {delivered}টি ডেলিভারি',
  'home.chipOverdue': '{amount} সময় পার',
  'home.chipMismatch.one': '{count}টি মেলেনি',
  'home.chipMismatch.other': '{count}টি মেলেনি',
  'home.chipReturned': 'আজ {count}টি ফেরত',

  'home.codEyebrow': 'বাকি COD · এখন কুরিয়ারের কাছে',
  'home.codNothing': 'কুরিয়ারের কাছে কিছু নেই।',
  'home.codExpectedToday':
      'আপনার নিজের সেটেলমেন্টের হিসাব অনুযায়ী আজ {amount} আসার কথা।',
  'home.codNoDate':
      'কবে আসবে এখনও বলা যাচ্ছে না — এই কুরিয়ারগুলোর সেটেলমেন্টের যথেষ্ট '
      'হিসাব এখনও জমা হয়নি।',
  'home.codNothingToday': 'আজ কিছু আসার কথা নেই।',
  'home.realizedToday': 'আজ হাতে এসেছে',
  'home.deliveredCaption': '{count}টি ডেলিভারি',
  'home.profitToday': 'আজকের লাভ',
  'home.contribution': 'কন্ট্রিবিউশন',
  'home.mismatch': 'মেলেনি',
  'home.openCaption': '{count}টি বাকি',

  'home.quick.newOrder': 'নতুন অর্ডার',
  'home.quick.newOrderSub': 'পেস্ট বা নিজে লিখে',
  'home.quick.riskCheck': 'রিস্ক চেক',
  'home.quick.riskCheckSub': 'নম্বরের আগের রেকর্ড',
  'home.quick.addPayout': 'পেআউট যোগ করুন',
  'home.quick.addPayoutSub': 'API · CSV · ম্যানুয়াল',
  'home.quick.products': 'প্রোডাক্ট',
  'home.quick.productsSub': 'খরচ · স্টক · মার্জিন',

  'home.reviewAll': 'সব দেখুন',
  'home.todaysContribution': 'আজকের কন্ট্রিবিউশন লাভ',
  'home.openProfitBreakdown': 'লাভের বিস্তারিত দেখুন',
  'home.businessPulse': 'ব্যবসার হালচাল',
  'home.businessPulseSub': '৩০ দিনের লাভ, COD আর ডেলিভারির মান',
  'home.fullAnalytics': 'পুরো বিশ্লেষণ',
  'home.liveActivity': 'এখনকার ঘটনা',
  'home.liveActivitySub': 'শুধু টাকা আর কাজের ঘটনা',
  'home.allActivity': 'সব ঘটনা',
  'home.deliveredRate': '{rate}% ডেলিভারি',

  'chart.contributionProfit': 'কন্ট্রিবিউশন লাভ',
  'chart.contributionProfitSub':
      'শেষ ৩০ দিন · অনুমান করা হিসাব আলাদা দেখানো, মেশানো নয়',
  'chart.codPosition': 'COD-এর অবস্থা',
  'chart.codPositionSub': 'যে টাকা এখনও তোলা হয়নি, বয়স অনুযায়ী',
  'chart.deliveryFunnel': 'ডেলিভারির ধাপ',
  'chart.last30': 'শেষ ৩০ দিন',
  'chart.courierHealth': 'কুরিয়ারের মান',
  'chart.courierHealthSub': 'শুধু আপনার নিজের হিসাব · নমুনা বুঝে',
  'chart.topProducts': 'লাভে সবচেয়ে ভালো প্রোডাক্ট',
  'chart.topProductsSub': 'বিক্রি নয় · কন্ট্রিবিউশন লাভ',
  'chart.returnPressure': 'ফেরতের চাপ',
  'chart.returnPressureSub': 'ফেরত আসলে কোথায় টাকা খাচ্ছে',
  'chart.emptyNoSettled': 'শেষ ৩০ দিনে সেটেল হওয়া কোনো পার্সেল নেই।',
  'chart.emptyNoCod': 'এখন কুরিয়ারের কাছে কোনো টাকা নেই।',
  'chart.emptyNoDispatched': 'শেষ ৩০ দিনে কোনো পার্সেল পাঠানো হয়নি।',
  'chart.emptyNoCourierSample':
      'কুরিয়ারের মান বিচার করার মতো শেষ হওয়া পার্সেল এখনও নেই।',
  'chart.emptyNoDelivered': 'শেষ ৩০ দিনে ডেলিভারি হওয়া কোনো প্রোডাক্ট নেই।',
  'chart.emptyNoReturns': 'শেষ ৩০ দিনে কোনো ফেরত নেই।',

  'courier.finishedReturned': '{finished}টি শেষ · {returned}টি ফেরত',
  'courier.backRate': '{rate} ফেরত',

  'activity.nothingTitle': 'এখন আপনার কিছু দেখার নেই',
  'activity.nothingBody':
      'টাকা ঝুঁকিতে পড়লে এখানে দেখাবে — ডেলিভারি হয়েছে কিন্তু টাকা আসেনি, '
      'টাকা কম এসেছে, বা পার্সেল কুরিয়ারে আটকে আছে।',
};

// --------------------------------------------------------------------------- //
// Orders
// --------------------------------------------------------------------------- //

const Map<String, String> _ordersEn = <String, String>{
  'orders.eyebrow': 'Order → courier → COD → profit',
  'orders.title': 'Orders',
  'orders.description': 'Fast seller workflow, not an ERP table.',
  'orders.searchHint': 'Order number, name, or full number',
  'orders.filter.all': 'All',
  'orders.filter.draft': 'Draft',
  'orders.filter.confirmed': 'Confirmed',
  'orders.filter.packed': 'Packed',
  'orders.filter.withCourier': 'With courier',
  'orders.filter.completed': 'Completed',
  'orders.emptyTitle': 'No orders yet',
  'orders.emptyBody':
      'Type one in, or paste a message from Messenger and check what it found.',
  'orders.emptyAction': 'Add your first order',
  'orders.savedOffline':
      'Saved on this phone. It will sync when you are back online.',
  'orders.savedNumber': 'Order {number} saved.',
  'orders.pasteTooltip': 'Paste an order',
  'orders.newOrder': 'New order',
  'orders.pendingWaiting.one': '{count} change waiting',
  'orders.pendingWaiting.other': '{count} changes waiting',
  'orders.pendingDetail': 'Saved on this phone and not on the server yet.',
  'orders.syncNow': 'Sync now',
  'orders.notNumbered': 'Not yet numbered',
  'orders.fact.cod': 'COD',
  'orders.fact.courier': 'Courier',
  'orders.fact.risk': 'Risk',
  'orders.fact.profit': 'Profit',
};

const Map<String, String> _ordersBn = <String, String>{
  'orders.eyebrow': 'অর্ডার → কুরিয়ার → COD → লাভ',
  'orders.title': 'অর্ডার',
  'orders.description': 'বিক্রেতার দ্রুত কাজের জায়গা, ভারী ERP টেবিল নয়।',
  'orders.searchHint': 'অর্ডার নম্বর, নাম, বা পুরো ফোন নম্বর',
  'orders.filter.all': 'সব',
  'orders.filter.draft': 'ড্রাফট',
  'orders.filter.confirmed': 'কনফার্ম',
  'orders.filter.packed': 'প্যাক করা',
  'orders.filter.withCourier': 'কুরিয়ারে',
  'orders.filter.completed': 'শেষ',
  'orders.emptyTitle': 'এখনও কোনো অর্ডার নেই',
  'orders.emptyBody':
      'নিজে লিখে দিন, বা Messenger থেকে মেসেজ পেস্ট করে দেখুন কী পাওয়া গেল।',
  'orders.emptyAction': 'প্রথম অর্ডারটি যোগ করুন',
  'orders.savedOffline': 'এই ফোনে সেভ হয়েছে। অনলাইনে ফিরলে সিঙ্ক হয়ে যাবে।',
  'orders.savedNumber': 'অর্ডার {number} সেভ হয়েছে।',
  'orders.pasteTooltip': 'অর্ডার পেস্ট করুন',
  'orders.newOrder': 'নতুন অর্ডার',
  'orders.pendingWaiting.one': '{count}টি পরিবর্তন অপেক্ষায়',
  'orders.pendingWaiting.other': '{count}টি পরিবর্তন অপেক্ষায়',
  'orders.pendingDetail': 'এই ফোনে সেভ আছে, সার্ভারে এখনও যায়নি।',
  'orders.syncNow': 'এখনই সিঙ্ক করুন',
  'orders.notNumbered': 'এখনও নম্বর হয়নি',
  'orders.fact.cod': 'COD',
  'orders.fact.courier': 'কুরিয়ার',
  'orders.fact.risk': 'রিস্ক',
  'orders.fact.profit': 'লাভ',
};

// --------------------------------------------------------------------------- //
// Money
// --------------------------------------------------------------------------- //

const Map<String, String> _moneyEn = <String, String>{
  'money.eyebrow': 'Courier → COD → payout → matched',
  'money.title': 'Money',
  'money.description': 'What the courier owes you, and what has arrived.',
  'money.couldNotLoadTitle': 'Could not load your money',
  'money.withCourierNow': 'With the courier now',
  'money.unpaidParcels.one': '{count} delivered parcel not paid yet',
  'money.unpaidParcels.other': '{count} delivered parcels not paid yet',
  'money.overAWeek': '{amount} over a week',
  'money.arrived': 'Arrived',
  'money.deducted': 'Deducted',
  'money.needsYou': 'Needs you',
  'money.casesBanner.one': '{count} thing needs you',
  'money.casesBanner.other': '{count} things need you',
  'money.casesDetail':
      'Money that did not arrive, arrived short, or could not be placed.',
  'money.unexplained': '{amount} unexplained',
  'money.unexplainedDetail':
      'Money arrived that has not been tied to a parcel yet. Open the payout '
      'to match it.',
  'money.payouts': 'Payouts',
  'money.agingTitle': 'How long it has been waiting',
  'money.agingSub': 'Outstanding COD by age',
  'money.deductionsTitle': 'What the courier took',
  'money.deductionsSub': 'Charges deducted from your money',
  'money.nothingWaiting':
      'Nothing is waiting. Every delivered parcel has been paid.',
  'money.bandLabel.one': '{band} · {count} parcel',
  'money.bandLabel.other': '{band} · {count} parcels',
  'money.deduction.delivery': 'Delivery charges',
  'money.deduction.codFee': 'COD fees',
  'money.deduction.returnCharge': 'Return charges',
  'money.deduction.notExplained': 'Not explained',
  'money.unknownNote':
      'We could not tell what the courier took this for. It is shown '
      'separately so you can ask them.',
  'money.writtenOff': 'Written off',
  'money.receivables': 'Receivables',
  'money.receivablesSub': 'Parcel by parcel',
  'money.payoutsSub': 'Statements and matching',
};

const Map<String, String> _moneyBn = <String, String>{
  'money.eyebrow': 'কুরিয়ার → COD → পেআউট → মিলেছে',
  'money.title': 'টাকা',
  'money.description': 'কুরিয়ারের কাছে কত পাওনা, আর কত এসেছে।',
  'money.couldNotLoadTitle': 'আপনার টাকার হিসাব লোড করা যায়নি',
  'money.withCourierNow': 'এখন কুরিয়ারের কাছে',
  'money.unpaidParcels.one': '{count}টি ডেলিভারি হওয়া পার্সেলের টাকা বাকি',
  'money.unpaidParcels.other': '{count}টি ডেলিভারি হওয়া পার্সেলের টাকা বাকি',
  'money.overAWeek': '{amount} এক সপ্তাহের বেশি',
  'money.arrived': 'এসেছে',
  'money.deducted': 'কেটেছে',
  'money.needsYou': 'দেখতে হবে',
  'money.casesBanner.one': '{count}টি বিষয় আপনার দেখা দরকার',
  'money.casesBanner.other': '{count}টি বিষয় আপনার দেখা দরকার',
  'money.casesDetail':
      'যে টাকা আসেনি, কম এসেছে, বা কোথায় বসবে ঠিক করা যায়নি।',
  'money.unexplained': '{amount} হিসাব মেলেনি',
  'money.unexplainedDetail':
      'টাকা এসেছে কিন্তু কোন পার্সেলের তা এখনও মেলানো হয়নি। পেআউট খুলে '
      'মিলিয়ে নিন।',
  'money.payouts': 'পেআউট',
  'money.agingTitle': 'কত দিন ধরে আটকে আছে',
  'money.agingSub': 'বাকি COD, বয়স অনুযায়ী',
  'money.deductionsTitle': 'কুরিয়ার কী কেটেছে',
  'money.deductionsSub': 'আপনার টাকা থেকে কাটা চার্জ',
  'money.nothingWaiting':
      'কিছু বাকি নেই। ডেলিভারি হওয়া প্রতিটি পার্সেলের টাকা এসে গেছে।',
  'money.bandLabel.one': '{band} · {count}টি পার্সেল',
  'money.bandLabel.other': '{band} · {count}টি পার্সেল',
  'money.deduction.delivery': 'ডেলিভারি চার্জ',
  'money.deduction.codFee': 'COD ফি',
  'money.deduction.returnCharge': 'ফেরতের চার্জ',
  'money.deduction.notExplained': 'ব্যাখ্যা নেই',
  'money.unknownNote':
      'কুরিয়ার এটি কী বাবদ কেটেছে বোঝা যায়নি। আপনি জিজ্ঞেস করতে পারেন বলে '
      'এটি আলাদা করে দেখানো হচ্ছে।',
  'money.writtenOff': 'রাইট-অফ করা',
  'money.receivables': 'পাওনা',
  'money.receivablesSub': 'পার্সেল ধরে ধরে',
  'money.payoutsSub': 'স্টেটমেন্ট আর মেলানো',
};

// --------------------------------------------------------------------------- //
// Insights
// --------------------------------------------------------------------------- //

const Map<String, String> _insightsEn = <String, String>{
  'insights.eyebrow': 'Accumulated-history advantage',
  'insights.title': 'Profit & business intelligence',
  'insights.description': 'Only insights that your own data can support.',
  'insights.couldNotLoadProfit': 'Could not load your profit figures.',
  'insights.ownCosts': 'Costs you enter yourself',
  'insights.ownCostsSub': 'Ad spend, packaging, rent',
  'insights.expenses': 'Expenses',
  'insights.bridge': 'Revenue → profit bridge',
  'insights.bridgeSub': 'Where the money goes',
  'insights.profitByProduct': 'Profit by product',
  'insights.profitByProductSub': 'Contribution profit · ranked',
  'insights.courierScorecard': 'Courier scorecard',
  'insights.courierScorecardSub': 'Own history · visible sample size',
  'insights.returnLossMap': 'Return loss map',
  'insights.returnLossMapSub': 'By product and area',
  'insights.profitEyebrow': 'Contribution profit · last 30 days',
  'insights.noParcelsWindow': 'No parcels have finished in this window yet.',
  'insights.settledOf.one':
      '{measured} of {count} parcel settled. Estimated inputs are marked, '
      'never blended in.',
  'insights.settledOf.other':
      '{measured} of {count} parcels settled. Estimated inputs are marked, '
      'never blended in.',
  'insights.deliveredSales': 'Delivered sales',
  'insights.realized': 'realized',
  'insights.returnLoss': 'Return loss',
  'insights.returnsCaption': '{count} returns',
  'insights.afterFixed': 'After fixed costs',
  'insights.operating': 'operating',
  'insights.qualityNote':
      'Estimated inputs are marked rather than blended in. A profit figure the '
      'app is not sure about never prints as exact.',
  'insights.unallocatedAds':
      '{amount} of ad spend has not been allocated to any parcel, so it sits '
      'below contribution profit rather than being spread across unrelated '
      'orders.',
  'insights.detailMissing.one': 'a cost is missing on {count} parcel',
  'insights.detailMissing.other': 'a cost is missing on {count} parcels',
  'insights.detailEstimated.one': '{count} parcel not settled yet',
  'insights.detailEstimated.other': '{count} parcels not settled yet',
  'insights.detailAllSettled': 'every input settled',
  'insights.whyBack': 'Why parcels came back',
  'insights.returnsInWindow': '{count} returns in the last 30 days',
  'insights.noReasonRecorded.one':
      '{count} return with no reason recorded. Adding one when you log a '
      'return makes this list worth acting on.',
  'insights.noReasonRecorded.other':
      '{count} returns with no reason recorded. Adding one when you log a '
      'return makes this list worth acting on.',
  'insights.thin': 'thin',
  'insights.finishedUnit': 'finished',
  'insights.rateRowSub': '{count} {unit} · {returned} back',
  'bridge.revenue': 'Revenue',
  'bridge.goods': 'Goods',
  'bridge.delivery': 'Delivery',
  'bridge.codFee': 'COD fee',
  'bridge.returns': 'Returns',
  'bridge.packaging': 'Packaging',
  'bridge.ads': 'Ads',
  'bridge.writeOffs': 'Write-offs',
  'bridge.profit': 'Profit',
};

const Map<String, String> _insightsBn = <String, String>{
  'insights.eyebrow': 'জমা হওয়া হিসাবের সুবিধা',
  'insights.title': 'লাভ আর ব্যবসার বিশ্লেষণ',
  'insights.description':
      'শুধু সেই বিশ্লেষণ, যেটা আপনার নিজের তথ্য দিয়ে বলা যায়।',
  'insights.couldNotLoadProfit': 'আপনার লাভের হিসাব লোড করা যায়নি।',
  'insights.ownCosts': 'যে খরচ আপনি নিজে লেখেন',
  'insights.ownCostsSub': 'বিজ্ঞাপন, প্যাকেজিং, ভাড়া',
  'insights.expenses': 'খরচ',
  'insights.bridge': 'বিক্রি → লাভের ধাপ',
  'insights.bridgeSub': 'টাকা কোথায় যাচ্ছে',
  'insights.profitByProduct': 'প্রোডাক্ট অনুযায়ী লাভ',
  'insights.profitByProductSub': 'কন্ট্রিবিউশন লাভ · ক্রম অনুযায়ী',
  'insights.courierScorecard': 'কুরিয়ারের স্কোরকার্ড',
  'insights.courierScorecardSub': 'নিজের হিসাব · নমুনার সংখ্যা দেখানো',
  'insights.returnLossMap': 'ফেরতে ক্ষতির মানচিত্র',
  'insights.returnLossMapSub': 'প্রোডাক্ট আর এলাকা অনুযায়ী',
  'insights.profitEyebrow': 'কন্ট্রিবিউশন লাভ · শেষ ৩০ দিন',
  'insights.noParcelsWindow': 'এই সময়ে এখনও কোনো পার্সেল শেষ হয়নি।',
  'insights.settledOf.one':
      '{count}টির মধ্যে {measured}টি পার্সেল সেটেল হয়েছে। অনুমান করা হিসাব '
      'আলাদা দেখানো হয়, কখনও মিশিয়ে দেওয়া হয় না।',
  'insights.settledOf.other':
      '{count}টির মধ্যে {measured}টি পার্সেল সেটেল হয়েছে। অনুমান করা হিসাব '
      'আলাদা দেখানো হয়, কখনও মিশিয়ে দেওয়া হয় না।',
  'insights.deliveredSales': 'ডেলিভারি হওয়া বিক্রি',
  'insights.realized': 'হাতে এসেছে',
  'insights.returnLoss': 'ফেরতে ক্ষতি',
  'insights.returnsCaption': '{count}টি ফেরত',
  'insights.afterFixed': 'নির্দিষ্ট খরচ বাদে',
  'insights.operating': 'অপারেটিং',
  'insights.qualityNote':
      'অনুমান করা হিসাব মিশিয়ে না দিয়ে আলাদা চিহ্নিত করা হয়। অ্যাপ যে '
      'লাভের হিসাব নিয়ে নিশ্চিত নয়, সেটি কখনও নিখুঁত বলে দেখানো হয় না।',
  'insights.unallocatedAds':
      '{amount} বিজ্ঞাপনের খরচ এখনও কোনো পার্সেলের সাথে বসানো হয়নি, তাই '
      'সেটি কন্ট্রিবিউশন লাভের নিচে আলাদা আছে — অন্য অর্ডারের ওপর ছড়িয়ে '
      'দেওয়া হয়নি।',
  'insights.detailMissing.one': '{count}টি পার্সেলে একটি খরচ নেই',
  'insights.detailMissing.other': '{count}টি পার্সেলে একটি খরচ নেই',
  'insights.detailEstimated.one': '{count}টি পার্সেল এখনও সেটেল হয়নি',
  'insights.detailEstimated.other': '{count}টি পার্সেল এখনও সেটেল হয়নি',
  'insights.detailAllSettled': 'সব হিসাব সেটেল',
  'insights.whyBack': 'পার্সেল কেন ফেরত এসেছে',
  'insights.returnsInWindow': 'শেষ ৩০ দিনে {count}টি ফেরত',
  'insights.noReasonRecorded.one':
      '{count}টি ফেরতের কোনো কারণ লেখা নেই। ফেরত লেখার সময় কারণ দিলে এই '
      'তালিকা দেখে কাজ করা যাবে।',
  'insights.noReasonRecorded.other':
      '{count}টি ফেরতের কোনো কারণ লেখা নেই। ফেরত লেখার সময় কারণ দিলে এই '
      'তালিকা দেখে কাজ করা যাবে।',
  'insights.thin': 'কম নমুনা',
  'insights.finishedUnit': 'শেষ',
  'insights.rateRowSub': '{count}টি {unit} · {returned}টি ফেরত',
  'bridge.revenue': 'বিক্রি',
  'bridge.goods': 'পণ্যের দাম',
  'bridge.delivery': 'ডেলিভারি',
  'bridge.codFee': 'COD ফি',
  'bridge.returns': 'ফেরত',
  'bridge.packaging': 'প্যাকেজিং',
  'bridge.ads': 'বিজ্ঞাপন',
  'bridge.writeOffs': 'রাইট-অফ',
  'bridge.profit': 'লাভ',
};

// --------------------------------------------------------------------------- //
// Menu
// --------------------------------------------------------------------------- //

const Map<String, String> _menuEn = <String, String>{
  'menu.operations': 'Operations',
  'menu.moneyData': 'Money & data',
  'menu.accountGrowth': 'Account & growth',
  'menu.closeMenu': 'Close menu',
  'menu.products': 'Products & stock',
  'menu.productsSub': 'Cost · margin · inventory',
  'menu.customers': 'Customers',
  'menu.customersSub': 'Private CRM · repeat buyers',
  'menu.riskCheck': 'Risk check',
  'menu.riskCheckSub': 'Delivery history signal',
  'menu.courierAccounts': 'Courier accounts',
  'menu.courierAccountsSub': 'BYOC · provider health',
  'menu.returnCenter': 'Return center',
  'menu.returnCenterSub': 'Loss · reasons · stock',
  'menu.expenses': 'Expenses & ads',
  'menu.expensesSub': 'Profit inputs · allocation',
  'menu.reconciliation': 'Reconciliation',
  'menu.reconciliationSub': 'Match · mismatch · dispute',
  'menu.payouts': 'Payouts',
  'menu.payoutsSub': 'API · CSV · manual',
  'menu.imports': 'Imports / exports',
  'menu.importsSub': 'Sheets · statements · own data',
  'menu.notifications': 'Notifications',
  'menu.notificationsSub': 'Actionable alerts',
  'menu.team': 'Team',
  'menu.teamSub': 'Roles · activity',
  'menu.subscription': 'Subscription',
  'menu.subscriptionSub': 'Plan · usage · billing',
  'menu.settings': 'Settings',
  'menu.settingsSub': 'Language · devices · privacy',
  'menu.support': 'Support',
  'menu.supportSub': 'Case + WhatsApp',
};

const Map<String, String> _menuBn = <String, String>{
  'menu.operations': 'কাজকর্ম',
  'menu.moneyData': 'টাকা ও তথ্য',
  'menu.accountGrowth': 'অ্যাকাউন্ট ও বৃদ্ধি',
  'menu.closeMenu': 'মেনু বন্ধ করুন',
  'menu.products': 'প্রোডাক্ট ও স্টক',
  'menu.productsSub': 'খরচ · মার্জিন · স্টক',
  'menu.customers': 'কাস্টমার',
  'menu.customersSub': 'নিজের CRM · বারবার কেনা কাস্টমার',
  'menu.riskCheck': 'রিস্ক চেক',
  'menu.riskCheckSub': 'ডেলিভারির আগের রেকর্ড',
  'menu.courierAccounts': 'কুরিয়ার অ্যাকাউন্ট',
  'menu.courierAccountsSub': 'নিজের অ্যাকাউন্ট · সার্ভিসের অবস্থা',
  'menu.returnCenter': 'রিটার্ন সেন্টার',
  'menu.returnCenterSub': 'ক্ষতি · কারণ · স্টক',
  'menu.expenses': 'খরচ ও বিজ্ঞাপন',
  'menu.expensesSub': 'লাভের হিসাবের খরচ · ভাগ',
  'menu.reconciliation': 'হিসাব মেলানো',
  'menu.reconciliationSub': 'মিলেছে · মেলেনি · অভিযোগ',
  'menu.payouts': 'পেআউট',
  'menu.payoutsSub': 'API · CSV · ম্যানুয়াল',
  'menu.imports': 'ইমপোর্ট / এক্সপোর্ট',
  'menu.importsSub': 'শিট · স্টেটমেন্ট · নিজের তথ্য',
  'menu.notifications': 'নোটিফিকেশন',
  'menu.notificationsSub': 'কাজে লাগে এমন জরুরি খবর',
  'menu.team': 'টিম',
  'menu.teamSub': 'দায়িত্ব · কাজের রেকর্ড',
  'menu.subscription': 'সাবস্ক্রিপশন',
  'menu.subscriptionSub': 'প্ল্যান · ব্যবহার · বিল',
  'menu.settings': 'সেটিংস',
  'menu.settingsSub': 'ভাষা · ডিভাইস · প্রাইভেসি',
  'menu.support': 'সাপোর্ট',
  'menu.supportSub': 'কেস + WhatsApp',
};

// --------------------------------------------------------------------------- //
// Notification centre
// --------------------------------------------------------------------------- //

const Map<String, String> _notifEn = <String, String>{
  'notif.eyebrow': 'Everything the app has told you',
  'notif.title': 'Notifications',
  'notif.description':
      'Kept here whether or not a push arrived, so nothing about your money '
      'depends on catching one.',
  'notif.everything': 'Everything',
  'notif.unread': 'Unread',
  'notif.nothingUnread': 'Nothing unread',
  'notif.yourWeek': 'Your week',
  'week.orders': 'Orders',
  'week.delivered': 'Delivered',
  'week.returned': 'Returned',
  'week.sales': 'Sales',
  'week.contributionProfit': 'Contribution profit',
  'week.returnLoss': 'Return loss',
  'week.adSpend': 'Ad spend',
  'week.codOutstanding': 'COD outstanding',
  'week.overdue': 'Overdue',
  'week.openMismatches': 'Open mismatches',
  'week.bestProduct': 'Best product',
  'week.worstProduct': 'Worst product',
  'week.bestCourier': 'Best courier',
  'week.worstCourier': 'Worst courier',
};

const Map<String, String> _notifBn = <String, String>{
  'notif.eyebrow': 'অ্যাপ আপনাকে যা যা জানিয়েছে',
  'notif.title': 'নোটিফিকেশন',
  'notif.description':
      'পুশ এলো কি না তার ওপর কিছু নির্ভর করে না — সবকিছু এখানেই জমা থাকে।',
  'notif.everything': 'সব',
  'notif.unread': 'অপঠিত',
  'notif.nothingUnread': 'অপঠিত কিছু নেই',
  'notif.yourWeek': 'আপনার সপ্তাহ',
  'week.orders': 'অর্ডার',
  'week.delivered': 'ডেলিভারি',
  'week.returned': 'ফেরত',
  'week.sales': 'বিক্রি',
  'week.contributionProfit': 'কন্ট্রিবিউশন লাভ',
  'week.returnLoss': 'ফেরতে ক্ষতি',
  'week.adSpend': 'বিজ্ঞাপন খরচ',
  'week.codOutstanding': 'বাকি COD',
  'week.overdue': 'সময় পার',
  'week.openMismatches': 'যা এখনও মেলেনি',
  'week.bestProduct': 'সবচেয়ে ভালো প্রোডাক্ট',
  'week.worstProduct': 'সবচেয়ে খারাপ প্রোডাক্ট',
  'week.bestCourier': 'সবচেয়ে ভালো কুরিয়ার',
  'week.worstCourier': 'সবচেয়ে খারাপ কুরিয়ার',
};

// --------------------------------------------------------------------------- //
// Settings
// --------------------------------------------------------------------------- //

const Map<String, String> _settingsEn = <String, String>{
  'settings.title': 'Settings',
  'settings.signedInAs': 'Signed in as {role}',
  'settings.sectionLanguage': 'Language',
  'settings.sectionPlan': 'Plan',
  'settings.sectionCouriers': 'Couriers',
  'settings.sectionAccount': 'Account',
  'settings.sectionSync': 'Sync',
  'settings.sectionAbout': 'About',
  'settings.courierAccounts': 'Courier accounts',
  'settings.steadfastConnected': 'Steadfast connected {identifier}',
  'settings.steadfastReconnect': 'Steadfast needs reconnecting',
  'settings.connectSteadfast': 'Connect Steadfast to book from ecomsbd',
  'settings.courierDefault': 'Book, track and reconcile automatically',
  'settings.devices': 'Devices and sessions',
  'settings.devicesSub': 'See where you are signed in, and sign out',
  'settings.notifications': 'Notifications',
  'settings.notificationsSub': 'What we interrupt you about',
  'settings.privacy': 'Your data and privacy',
  'settings.privacySub': 'Export everything, or close your account',
  'settings.subscription': 'Subscription',
  'settings.paymentProblem': 'Payment problem — tap to fix',
  'settings.endsSoon': 'Ends soon',
  'settings.planName': '{plan} plan',
  'settings.couldNotCheckPlan': 'Could not check your plan',
  'settings.actionNeeded': 'Action needed',
  'settings.everythingSynced':
      'Everything on this phone has reached the server.',
  'settings.changesWaiting.one':
      '{count} change waiting to sync. Nothing is lost.',
  'settings.changesWaiting.other':
      '{count} changes waiting to sync. Nothing is lost.',
  'settings.checking': 'Checking…',
  'settings.couldNotReadQueue': 'Could not read the queue.',
  'settings.appVersion': 'App version',
  'settings.server': 'Server',
  'settings.signOutTitle': 'Sign out of this device?',
  'settings.signOutBody':
      'Anything waiting to sync will be sent first. You can sign back in with '
      'the same number.',
  'settings.needsConnection':
      '{what} needs a connection. Nothing is saved on this phone for it, '
      'because a stale answer here could be wrong.',
  'settings.thisWord': 'This',
};

const Map<String, String> _settingsBn = <String, String>{
  'settings.title': 'সেটিংস',
  'settings.signedInAs': '{role} হিসেবে সাইন ইন করা',
  'settings.sectionLanguage': 'ভাষা',
  'settings.sectionPlan': 'প্ল্যান',
  'settings.sectionCouriers': 'কুরিয়ার',
  'settings.sectionAccount': 'অ্যাকাউন্ট',
  'settings.sectionSync': 'সিঙ্ক',
  'settings.sectionAbout': 'অ্যাপ সম্পর্কে',
  'settings.courierAccounts': 'কুরিয়ার অ্যাকাউন্ট',
  'settings.steadfastConnected': 'Steadfast যুক্ত আছে {identifier}',
  'settings.steadfastReconnect': 'Steadfast আবার যুক্ত করতে হবে',
  'settings.connectSteadfast': 'ecomsbd থেকে বুক করতে Steadfast যুক্ত করুন',
  'settings.courierDefault': 'বুকিং, ট্র্যাকিং আর হিসাব মেলানো নিজেই হবে',
  'settings.devices': 'ডিভাইস ও সেশন',
  'settings.devicesSub': 'কোথায় কোথায় সাইন ইন আছেন দেখুন, সাইন আউট করুন',
  'settings.notifications': 'নোটিফিকেশন',
  'settings.notificationsSub': 'কী কী বিষয়ে আপনাকে জানানো হবে',
  'settings.privacy': 'আপনার তথ্য ও প্রাইভেসি',
  'settings.privacySub': 'সব তথ্য নামিয়ে নিন, বা অ্যাকাউন্ট বন্ধ করুন',
  'settings.subscription': 'সাবস্ক্রিপশন',
  'settings.paymentProblem': 'পেমেন্টে সমস্যা — ঠিক করতে চাপ দিন',
  'settings.endsSoon': 'শীঘ্রই শেষ হবে',
  'settings.planName': '{plan} প্ল্যান',
  'settings.couldNotCheckPlan': 'আপনার প্ল্যান দেখা যায়নি',
  'settings.actionNeeded': 'ব্যবস্থা নিতে হবে',
  'settings.everythingSynced': 'এই ফোনের সবকিছু সার্ভারে পৌঁছে গেছে।',
  'settings.changesWaiting.one':
      '{count}টি পরিবর্তন সিঙ্কের অপেক্ষায়। কিছু হারায়নি।',
  'settings.changesWaiting.other':
      '{count}টি পরিবর্তন সিঙ্কের অপেক্ষায়। কিছু হারায়নি।',
  'settings.checking': 'দেখা হচ্ছে…',
  'settings.couldNotReadQueue': 'অপেক্ষমাণ তালিকা পড়া যায়নি।',
  'settings.appVersion': 'অ্যাপ ভার্সন',
  'settings.server': 'সার্ভার',
  'settings.signOutTitle': 'এই ডিভাইস থেকে সাইন আউট করবেন?',
  'settings.signOutBody':
      'সিঙ্কের অপেক্ষায় থাকা সবকিছু আগে পাঠিয়ে দেওয়া হবে। একই নম্বর দিয়ে '
      'আবার সাইন ইন করতে পারবেন।',
  'settings.needsConnection':
      '{what} দেখতে ইন্টারনেট লাগবে। এর কিছুই এই ফোনে সেভ রাখা হয় না, কারণ '
      'পুরোনো তথ্য এখানে ভুল হতে পারে।',
  'settings.thisWord': 'এটি',
};

// --------------------------------------------------------------------------- //
// OTP verification
// --------------------------------------------------------------------------- //

const Map<String, String> _otpEn = <String, String>{
  'otp.title': 'Enter the code',
  'otp.sentTo': 'A 6-digit code was sent to {phone}.',
  'otp.verify': 'Verify',
  'otp.resendIn': 'Ask for a new code in {count} seconds',
  'otp.changeNumber': 'Change the number, or get a new code',
  'otp.attemptsRemaining.one': '{count} attempt left.',
  'otp.attemptsRemaining.other': '{count} attempts left.',
};

const Map<String, String> _otpBn = <String, String>{
  'otp.title': 'কোড দিন',
  'otp.sentTo': '{phone} নম্বরে ৬ সংখ্যার কোড পাঠানো হয়েছে।',
  'otp.verify': 'যাচাই করুন',
  'otp.resendIn': 'আবার কোড চান {count} সেকেন্ড পরে',
  'otp.changeNumber': 'নম্বর বদলান বা আবার কোড নিন',
  'otp.attemptsRemaining.one': 'আর {count} বার চেষ্টা করতে পারবেন।',
  'otp.attemptsRemaining.other': 'আর {count} বার চেষ্টা করতে পারবেন।',
};

// --------------------------------------------------------------------------- //
// Courier booking, and the money field labels
// --------------------------------------------------------------------------- //

const Map<String, String> _fieldsEn = <String, String>{
  'booking.ambiguousFallback':
      'The booking result is not confirmed — do not book it again. The '
      'earlier attempt is being checked.',
  'field.amountTaka': 'Amount (৳)',
  'field.codToCollect': 'COD to collect (৳)',
  'field.deliveryFee': 'Delivery fee (৳)',
  'field.unitPrice': 'Unit price (৳)',
  'field.cost': 'Cost (৳)',
  'field.sellingPrice': 'Selling price (৳)',
};

const Map<String, String> _fieldsBn = <String, String>{
  // "Booking result" is what sellers say on the ground, so it stays.
  'booking.ambiguousFallback':
      'Booking result নিশ্চিত হয়নি — আবার বুক করবেন না। '
      'আগের চেষ্টা যাচাই করা হচ্ছে।',
  'field.amountTaka': 'টাকার পরিমাণ (৳)',
  'field.codToCollect': 'যত COD আদায় হবে (৳)',
  'field.deliveryFee': 'ডেলিভারি ফি (৳)',
  'field.unitPrice': 'প্রতি পিসের দাম (৳)',
  'field.cost': 'কেনা দাম (৳)',
  'field.sellingPrice': 'বিক্রির দাম (৳)',
};

// --------------------------------------------------------------------------- //
// Auth failures (selected by stable code; SDK/server wording stays private)
// --------------------------------------------------------------------------- //

const Map<String, String> _authErrEn = <String, String>{
  'autherr.invalidCredentials':
      'That email and password do not match. Please try again.',
  'autherr.offline':
      'No internet connection. Check your connection and try again.',
  // Android reports a dismissal and a rejected OAuth registration
  // identically, so this must not accuse the seller of cancelling.
  'autherr.googleIncomplete':
      'Google sign-in did not finish. If you did not close it yourself, this '
      'app is not registered for Google sign-in yet — please use email.',
  'autherr.googleCancelled': 'Google sign-in was cancelled. You can try again.',
  'autherr.appleCancelled': 'Apple sign-in was cancelled. You can try again.',
  'autherr.googleUnavailable':
      'Google sign-in is unavailable right now. Use email or try again later.',
  'autherr.appleUnavailable':
      'Apple sign-in is unavailable on this device. Use Google or email.',
  'autherr.emailNotVerified':
      'Verify your email using the link in your inbox, then sign in.',
  'autherr.emailAlreadyRegistered':
      'An account already uses this email. Sign in or reset your password.',
  'autherr.identityLinkRefused':
      'This sign-in method belongs to another account. Use your original '
      'sign-in method.',
  'autherr.linkExpired':
      'This link or sign-in has expired. Request a new link or sign in again.',
  'autherr.rateLimited':
      'Too many attempts. Please wait a while before trying again.',
  'autherr.validation':
      'Check your email and password. Use 10–200 characters and avoid common '
      'passwords.',
  'autherr.methodUnavailable':
      'This sign-in method is unavailable right now. Please try another '
      'method.',
  'autherr.forbidden': 'This account cannot sign in. Contact support for help.',
  'autherr.generic': 'Sign-in could not be completed. Please try again.',
};

const Map<String, String> _authErrBn = <String, String>{
  'autherr.invalidCredentials':
      'এই ইমেইল আর পাসওয়ার্ড মিলছে না। আবার চেষ্টা করুন।',
  'autherr.offline': 'ইন্টারনেট সংযোগ নেই। সংযোগ দেখে আবার চেষ্টা করুন।',
  'autherr.googleIncomplete':
      'Google সাইন ইন শেষ হয়নি। আপনি নিজে বন্ধ না করে থাকলে বুঝতে হবে এই '
      'অ্যাপটি এখনও Google সাইন ইনের জন্য রেজিস্টার করা হয়নি — আপাতত ইমেইল '
      'দিয়ে সাইন ইন করুন।',
  'autherr.googleCancelled':
      'Google সাইন ইন বাতিল হয়েছে। আবার চেষ্টা করতে পারেন।',
  'autherr.appleCancelled':
      'Apple সাইন ইন বাতিল হয়েছে। আবার চেষ্টা করতে পারেন।',
  'autherr.googleUnavailable':
      'Google সাইন ইন এখন কাজ করছে না। ইমেইল দিয়ে করুন বা পরে চেষ্টা করুন।',
  'autherr.appleUnavailable':
      'এই ফোনে Apple সাইন ইন কাজ করবে না। Google বা ইমেইল ব্যবহার করুন।',
  'autherr.emailNotVerified':
      'ইনবক্সের লিংক দিয়ে ইমেইল ভেরিফাই করে তারপর সাইন ইন করুন।',
  'autherr.emailAlreadyRegistered':
      'এই ইমেইলে একটি অ্যাকাউন্ট আছে। সাইন ইন করুন বা পাসওয়ার্ড রিসেট করুন।',
  'autherr.identityLinkRefused':
      'এই সাইন ইন পদ্ধতি অন্য একটি অ্যাকাউন্টের। আগে যেভাবে সাইন ইন করতেন '
      'সেভাবেই করুন।',
  'autherr.linkExpired':
      'এই লিংক বা সাইন ইনের সময় শেষ। নতুন লিংক নিন বা আবার সাইন ইন করুন।',
  'autherr.rateLimited':
      'অনেকবার চেষ্টা হয়েছে। কিছুক্ষণ পরে আবার চেষ্টা করুন।',
  'autherr.validation':
      'ইমেইল আর পাসওয়ার্ড দেখে নিন। ১০–২০০ অক্ষর দিন, খুব সহজ পাসওয়ার্ড '
      'এড়িয়ে চলুন।',
  'autherr.methodUnavailable':
      'এই সাইন ইন পদ্ধতি এখন কাজ করছে না। অন্য পদ্ধতি ব্যবহার করুন।',
  'autherr.forbidden':
      'এই অ্যাকাউন্ট দিয়ে সাইন ইন করা যাবে না। সাপোর্টে যোগাযোগ করুন।',
  'autherr.generic': 'সাইন ইন সম্পূর্ণ করা যায়নি। আবার চেষ্টা করুন।',
};

// --------------------------------------------------------------------------- //
// Shared components: hero, attention card, badges, chart empty state
// --------------------------------------------------------------------------- //

const Map<String, String> _componentsEn = <String, String>{
  'home.heroEyebrow': 'TODAY · MONEY CONTROL',
  'attention.title': 'Needs attention',
  'attention.subtitle': 'Only issues that can cost money or require action',
  'chart.notEnoughHistory': 'Not enough history yet',
  // Risk wording is about the order, never the person (master spec 130).
  'risk.low': 'Low risk',
  'risk.medium': 'Medium risk',
  'risk.high': 'High risk',
  'risk.unknown': 'No history',
  'risk.lowShort': 'Low',
  'risk.mediumShort': 'Medium',
  'risk.highShort': 'High',
  'risk.unknownShort': 'None',
  'provider.connected': 'Connected',
  'provider.notConnected': 'Not connected',
  'provider.manualOnly': 'Manual only',
  'provider.degraded': 'Degraded',
  'quality.actual': 'Actual',
  'quality.estimated': 'Estimated',
  'quality.missing': 'Missing cost',
  'quality.unreconciled': 'Unreconciled',
};

const Map<String, String> _componentsBn = <String, String>{
  'home.heroEyebrow': 'আজ · টাকার নিয়ন্ত্রণ',
  'attention.title': 'যা দেখতে হবে',
  'attention.subtitle': 'যেসব বিষয়ে টাকা যেতে পারে বা ব্যবস্থা নিতে হবে',
  'chart.notEnoughHistory': 'এখনও যথেষ্ট হিসাব জমা হয়নি',
  'risk.low': 'কম ঝুঁকি',
  'risk.medium': 'মাঝারি ঝুঁকি',
  'risk.high': 'বেশি ঝুঁকি',
  'risk.unknown': 'রেকর্ড নেই',
  'risk.lowShort': 'কম',
  'risk.mediumShort': 'মাঝারি',
  'risk.highShort': 'বেশি',
  'risk.unknownShort': 'নেই',
  'provider.connected': 'যুক্ত আছে',
  'provider.notConnected': 'যুক্ত নেই',
  'provider.manualOnly': 'শুধু ম্যানুয়াল',
  'provider.degraded': 'সমস্যা চলছে',
  'quality.actual': 'পাক্কা হিসাব',
  'quality.estimated': 'অনুমান',
  'quality.missing': 'খরচ জানা নেই',
  'quality.unreconciled': 'মেলানো হয়নি',
};
