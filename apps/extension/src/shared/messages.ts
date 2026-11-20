/** Side panel → content script: "read the open chat now" (after a Capture tap). */

import type { CapturedLine } from './conversation.ts';
import type { Host } from './rules.ts';

export const READ = 'ecomsbd.read';

export interface ReadRequest {
  type: typeof READ;
}

export type ReadResponse =
  | {
      kind: 'CONVERSATION';
      host: Host;
      conversation_key: string;
      sender_display: string;
      messages: CapturedLine[];
    }
  | { kind: 'SELECTION'; host: Host; messages: CapturedLine[]; supported: boolean }
  | { kind: 'NOTHING'; host: Host; reason: 'EMPTY' | 'TOO_LONG'; supported: boolean };
