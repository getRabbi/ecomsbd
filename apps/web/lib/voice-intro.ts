/**
 * Settings → Confirmation call voice (V4 WP3.3): types, copy and what to offer.
 *
 * Self-contained (no value imports) so `npm test` can load it with Node's type
 * stripping. The server decides everything that matters: a recording can be
 * turned on only after a seller played it (`VOICE_INTRO_NOT_PREVIEWED`), and a
 * rename retires the old name's recording. This file only chooses which
 * buttons to show for the status the server sent.
 */

export type IntroStatusCode = 'NONE' | 'READY' | 'ON';

export interface IntroVersion {
  version: number;
  state: 'READY' | 'ON' | 'RETIRED';
  source: 'TTS' | 'UPLOAD';
  duration_ms: number | null;
  previewed: boolean;
  created_at: string;
  approved_at: string | null;
}

export interface IntroStatus {
  shop_name: string;
  script: string;
  status: IntroStatusCode;
  latest: IntroVersion | null;
  live: IntroVersion | null;
  tts_available: boolean;
  calls_on: boolean;
  calls_off_reason: string | null;
}

export interface IntroOffer {
  /** The recording to listen to and turn on (newer than the one on), if any. */
  candidate: IntroVersion | null;
  /** Whether "Turn on" may be pressed for the candidate. */
  canTurnOn: boolean;
  /** Nothing to listen to yet: make one (TTS) or wait for the team. */
  canRequest: boolean;
  waitingForTeam: boolean;
  /** On, but calls are not running (the call service is not ready). */
  onButIdle: boolean;
}

/** `heard`: versions played in this page; the server's `previewed` also counts. */
export function introOffer(status: IntroStatus, heard: ReadonlySet<number> = new Set()): IntroOffer {
  const latest = status.latest;
  const candidate = latest && latest.state === 'READY' ? latest : null;
  const nothing = status.status === 'NONE';
  return {
    candidate,
    canTurnOn: candidate !== null && (candidate.previewed || heard.has(candidate.version)),
    canRequest: nothing && status.tts_available,
    waitingForTeam: nothing && !status.tts_available,
    onButIdle: status.status === 'ON' && !status.calls_on,
  };
}

export function durationLabel(ms: number | null): string | null {
  if (ms === null || ms <= 0) return null;
  return `${Math.max(1, Math.round(ms / 1000))}s`;
}

export const voiceIntroEn = {
  'nav.callVoice': 'Confirmation call voice',
  'vi.subtitle': 'What your customers hear when ecomsbd calls to confirm an order.',
  'vi.notEnabled': 'Confirmation calls are not turned on for your shop yet.',
  'vi.readOnly': 'Only the shop owner or a manager can change this.',
  'vi.scriptTitle': 'What the call says',
  'vi.scriptHint': 'Your shop name is spoken. If you rename your shop, a new recording is needed.',
  'vi.statusTitle': 'Recording',
  'vi.statusOn': 'Calls are on with this recording.',
  'vi.statusOnIdle': 'Turned on. Calls start when the call service is ready.',
  'vi.statusNone': 'No recording for your shop yet.',
  'vi.waitingForTeam': 'The ecomsbd team records this for your shop. It will appear here for you to listen to.',
  'vi.request': 'Make the recording',
  'vi.requested': 'Being made. Check back in a few minutes.',
  'vi.newReady': 'A recording is ready. Listen to it before turning calls on.',
  'vi.listen': 'Listen',
  'vi.listenAgain': 'Listen again',
  'vi.turnOn': 'Turn on calls with this voice',
  'vi.turnOnHint': 'Listen once first.',
  'vi.turnOff': 'Stop calls',
  'vi.version': 'Recording {version}',
  'vi.sourceTts': 'made automatically',
  'vi.sourceUpload': 'recorded by ecomsbd',
} as const;

export type VoiceIntroKey = keyof typeof voiceIntroEn;

export const voiceIntroBn: Record<VoiceIntroKey, string> = {
  'nav.callVoice': 'কনফার্মেশন কলের ভয়েস',
  'vi.subtitle': 'অর্ডার কনফার্ম করতে ecomsbd কল করলে আপনার কাস্টমার যা শুনবেন।',
  'vi.notEnabled': 'আপনার দোকানে কনফার্মেশন কল এখনো চালু হয়নি।',
  'vi.readOnly': 'শুধু দোকানের মালিক বা ম্যানেজার এটি বদলাতে পারেন।',
  'vi.scriptTitle': 'কলে যা বলা হবে',
  'vi.scriptHint': 'কলে আপনার দোকানের নাম বলা হয়। দোকানের নাম বদলালে নতুন রেকর্ডিং লাগবে।',
  'vi.statusTitle': 'রেকর্ডিং',
  'vi.statusOn': 'এই রেকর্ডিং দিয়ে কল চালু আছে।',
  'vi.statusOnIdle': 'চালু করা হয়েছে। কল সার্ভিস প্রস্তুত হলেই কল শুরু হবে।',
  'vi.statusNone': 'আপনার দোকানের জন্য এখনো কোনো রেকর্ডিং নেই।',
  'vi.waitingForTeam': 'ecomsbd টিম আপনার দোকানের জন্য এটি রেকর্ড করে দেবে। শোনার জন্য এখানেই পাবেন।',
  'vi.request': 'রেকর্ডিং তৈরি করুন',
  'vi.requested': 'তৈরি হচ্ছে। কয়েক মিনিট পরে দেখুন।',
  'vi.newReady': 'একটি রেকর্ডিং প্রস্তুত। কল চালুর আগে একবার শুনে নিন।',
  'vi.listen': 'শুনুন',
  'vi.listenAgain': 'আবার শুনুন',
  'vi.turnOn': 'এই ভয়েস দিয়ে কল চালু করুন',
  'vi.turnOnHint': 'আগে একবার শুনে নিন।',
  'vi.turnOff': 'কল বন্ধ করুন',
  'vi.version': 'রেকর্ডিং {version}',
  'vi.sourceTts': 'স্বয়ংক্রিয়ভাবে তৈরি',
  'vi.sourceUpload': 'ecomsbd-এর রেকর্ড করা',
};
