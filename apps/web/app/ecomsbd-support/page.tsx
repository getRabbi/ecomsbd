import type { Metadata } from 'next';
import Link from 'next/link';

import { privacyEmail } from '@/lib/legal';
import styles from '../privacy-policy/privacy.module.css';

export const metadata: Metadata = {
  title: 'Support | ecomsbd',
  description: 'Get help with ecomsbd: account and login, orders, couriers, store integrations, Messenger and WhatsApp order automation, notifications and account deletion.',
  alternates: { canonical: 'https://scalemyprints.com/ecomsbd-support' },
  robots: { index: true, follow: true },
};

const supportEmailLink = `mailto:${privacyEmail}?subject=${encodeURIComponent('ecomsbd support request')}`;

const topics = [
  'Account and login help',
  'Orders and customers',
  'Courier connections',
  'WooCommerce and website integrations',
  'Messenger and WhatsApp order automation',
  'Notifications',
  'Account deletion',
];

/** The App Store Support URL. Public: a seller who cannot sign in still needs it. */
export default function SupportPage() {
  return (
    <div className={styles.page} lang="en">
      <a className={styles.skip} href="#contact-support">Skip to contact details</a>
      <header className={styles.header}>
        <Link className={styles.brand} href="/ecomsbd">ecomsbd<span>Seller operations, simply</span></Link>
      </header>
      <main className={`${styles.main} ${styles.deletionMain}`}>
        <div className={styles.intro}>
          <p className={styles.eyebrow}>ECOMSBD · SUPPORT</p>
          <h1>ecomsbd Support</h1>
          <p className={styles.lead}>ecomsbd helps Bangladesh-based online sellers manage orders, customers, couriers, COD payments and connected sales channels.</p>
        </div>
        <article className={styles.article}>
          <section id="contact-support">
            <h2>Contact support</h2>
            <p>Email us with your question. Where possible, write from your account email, name your shop and describe what you were doing when the problem happened.</p>
            <div className={styles.requestContact}>
              <a href={supportEmailLink}>{privacyEmail}</a>
              <a className={styles.requestButton} href={supportEmailLink}>Email support</a>
            </div>
            <p><strong>When contacting support, do not send passwords, API keys or other secret credentials.</strong></p>
          </section>
          <section id="help-topics">
            <h2>Common help topics</h2>
            <p>We can help with:</p>
            <ul className={styles.steps}>{topics.map(topic => <li key={topic}>{topic}</li>)}</ul>
          </section>
          <section id="privacy-and-deletion">
            <h2>Privacy and account deletion</h2>
            <p>Read how ecomsbd handles your information in our <Link href="/privacy-policy">Privacy Policy</Link>.</p>
            <p>You can ask us to delete your account and its data without signing in. See <Link href="/account-deletion">Account Deletion</Link> for how.</p>
          </section>
        </article>
      </main>
      <footer className={styles.footer}>
        <span>ecomsbd · scalemyprints.com</span>
        <Link href="/ecomsbd">About ecomsbd</Link>
        <Link href="/privacy-policy">Privacy Policy</Link>
        <Link href="/account-deletion">Account deletion</Link>
      </footer>
    </div>
  );
}
