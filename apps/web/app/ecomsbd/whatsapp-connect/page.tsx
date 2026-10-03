'use client';

import { useEffect, useRef, useState } from 'react';
import { appLink, completionBody, loginOptions, metaMessage, signupRequest, signupState, type Assets, type Bootstrap, type SignupResult } from '@/lib/whatsapp-signup';

type Facebook = {
  init: (options: object) => void;
  login: (callback: (response: { authResponse?: { code?: string } }) => void, options: object) => void;
};
declare global { interface Window { FB?: Facebook; fbAsyncInit?: () => void } }

const api = process.env.NEXT_PUBLIC_API_BASE_URL ?? '';

export default function WhatsAppConnectPage() {
  const boot = useRef<Promise<Bootstrap> | null>(null);
  const session = useRef<Bootstrap | null>(null);
  const assets = useRef<Assets>({});
  const submission = useRef<ReturnType<typeof completionBody> | null>(null);
  const inFlight = useRef(false);
  const launched = useRef(false);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState('WhatsApp সংযোগ প্রস্তুত হচ্ছে…');
  const [back, setBack] = useState<string | null>(null);
  const [retry, setRetry] = useState(false);

  useEffect(() => {
    if (!boot.current) {
      const state = signupState(window.location.hash);
      // Remove the launch ticket before loading Meta or making requests.
      window.history.replaceState(null, '', window.location.pathname);
      boot.current = state && api
        ? signupRequest<Bootstrap>(api, 'bootstrap', { signup_state: state })
        : Promise.reject(new Error('STATE_INVALID'));
    }
    let active = true;
    void boot.current.then(config => {
      if (!active) return;
      session.current = config;
      setBack(appLink(config.connection_id, 'UNKNOWN'));
      const initialize = () => {
        if (!active || !window.FB) return;
        window.FB.init({ appId: config.app_id, version: config.graph_version, autoLogAppEvents: false, xfbml: false });
        setReady(true); setBusy(false); setMessage('Meta-তে সাইন ইন করে আপনার WhatsApp Business অ্যাকাউন্ট যুক্ত করুন।');
      };
      window.fbAsyncInit = initialize;
      if (window.FB) initialize();
      else {
        const script = document.createElement('script');
        script.src = 'https://connect.facebook.net/en_US/sdk.js';
        script.async = true; script.crossOrigin = 'anonymous';
        script.onerror = () => { if (active) { setBusy(false); setMessage('Meta এখন খোলা যাচ্ছে না। অ্যাপে ফিরে আবার চেষ্টা করুন।'); } };
        document.head.appendChild(script);
      }
    }).catch(() => {
      if (active) { setBusy(false); setMessage('এই সংযোগের মেয়াদ শেষ বা সেটআপ সাময়িকভাবে অনুপলব্ধ। ecomsbd অ্যাপ থেকে আবার শুরু করুন।'); }
    });
    const receive = (event: MessageEvent) => {
      if (!launched.current) return;
      const parsed = metaMessage(event.origin, event.data);
      if (parsed?.event === 'FINISH') assets.current = parsed.assets;
    };
    window.addEventListener('message', receive);
    return () => { active = false; window.removeEventListener('message', receive); };
  }, []);

  async function complete() {
    if (!submission.current || inFlight.current) return;
    inFlight.current = true; setBusy(true); setRetry(false);
    setMessage('সংযোগ যাচাই হচ্ছে…');
    try {
      const result = await signupRequest<SignupResult>(api, 'complete', submission.current);
      const link = appLink(result.connection_id, result.result);
      setBack(link); setReady(false);
      if (result.result === 'SIGNUP_IN_PROGRESS') {
        setMessage('সংযোগ যাচাই চলছে। কিছুক্ষণ পরে আবার যাচাই করুন।'); setRetry(true);
      } else {
        submission.current = null;
        setMessage(result.result === 'CONNECTED' ? 'WhatsApp যুক্ত হয়েছে। ecomsbd অ্যাপে ফিরে যান।'
          : result.result === 'ACCESS_DENIED' ? 'সংযোগ বাতিল হয়েছে। অ্যাপ থেকে আবার শুরু করতে পারবেন।'
          : result.result === 'META_APPROVAL_REQUIRED' ? 'জনসাধারণের জন্য WhatsApp সংযোগ চালু করতে Meta-এর অনুমোদন প্রয়োজন।'
          : 'WhatsApp সংযোগ সম্পূর্ণ হয়নি। অ্যাপে ফিরে আবার চেষ্টা করুন।');
        if (result.result === 'CONNECTED' && link) {
          // Use the existing HTTPS -> app redirect, with a visible fallback.
          const target = new URL(result.return_url);
          if (target.origin === new URL(api).origin && target.pathname === '/v1/integration-callbacks/return') window.location.assign(target.href);
        }
      }
    } catch {
      setMessage('সংযোগের ফল যাচাই করা যায়নি। আবার যাচাই করুন অথবা অ্যাপে ফিরুন।'); setRetry(true);
    } finally { inFlight.current = false; setBusy(false); }
  }

  function launch() {
    if (!session.current || !window.FB || launched.current) return;
    launched.current = true; assets.current = {}; setBusy(true); setReady(false);
    setMessage('Meta-তে সংযোগ সম্পূর্ণ করে এখানে ফিরে আসুন।');
    window.FB.login(response => {
      if (!session.current) return;
      submission.current = completionBody(session.current, response.authResponse?.code ?? null, assets.current);
      void complete();
    }, loginOptions(session.current.config_id));
  }

  return <main style={{ maxWidth: 480, margin: '64px auto', padding: 24 }}>
    <h1>WhatsApp যুক্ত করুন</h1>
    <p>আপনার WhatsApp Business অ্যাকাউন্ট যুক্ত করুন। Customer message থেকে order information ecomsbd Inbox-এ draft হিসেবে আসবে।</p>
    <p role="status" aria-live="polite">{message}</p>
    {ready && <button className="btn btn--primary" disabled={busy} onClick={launch}>WhatsApp দিয়ে যুক্ত করুন</button>}
    {retry && <button className="btn" disabled={busy} onClick={() => void complete()}>আবার যাচাই করুন</button>}
    <p><a href={back ?? 'com.ecomsbd.app://integrations/return'}>ecomsbd খুলুন</a></p>
  </main>;
}
