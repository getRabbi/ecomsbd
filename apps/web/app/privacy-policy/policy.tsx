import Link from 'next/link';
import { privacyEmail } from '@/lib/legal';

import { policySections, updated, type PolicyLocale } from './content';
import styles from './privacy.module.css';

export function PrivacyPolicy({ locale }: { locale: PolicyLocale }) {
  const bn = locale === 'bn';
  return (
    <div className={styles.page} lang={locale}>
      <a className={styles.skip} href="#policy">{bn ? 'নীতিতে যান' : 'Skip to policy'}</a>
      <header className={styles.header}>
        <Link className={styles.brand} href="/">ecomsbd<span>{bn ? 'বিক্রেতার কাজ, সহজভাবে' : 'Seller operations, simply'}</span></Link>
        <nav aria-label={bn ? 'নীতির ভাষা' : 'Policy language'} className={styles.languages}>
          <Link href="/privacy-policy" hrefLang="en" lang="en" aria-current={!bn ? 'page' : undefined}>English</Link>
          <Link href="/privacy-policy/bn" hrefLang="bn" lang="bn" aria-current={bn ? 'page' : undefined}>বাংলা</Link>
        </nav>
      </header>
      <main id="policy" className={styles.main}>
        <div className={styles.intro}>
          <p className={styles.eyebrow}>ECOMSBD · {bn ? 'গোপনীয়তা' : 'PRIVACY'}</p>
          <h1>{bn ? 'গোপনীয়তা নীতি' : 'Privacy Policy'}</h1>
          <p className={styles.lead}>{bn ? 'আপনার তথ্য, আপনার দোকান এবং আমরা যে সেবা দিই।' : 'Your information, your shop, and the services we provide.'}</p>
          <p className={styles.date}>{bn ? 'সর্বশেষ হালনাগাদ: ' : 'Last updated: '}<time dateTime={updated}>{bn ? '২৯ সেপ্টেম্বর ২০২৬' : '29 September 2026'}</time></p>
        </div>
        <div className={styles.layout}>
          <nav className={styles.contents} aria-label={bn ? 'সূচিপত্র' : 'On this page'}>
            <p>{bn ? 'এই পৃষ্ঠায়' : 'On this page'}</p>
            <ol>
              {policySections.map(section => <li key={section.id}><a href={`#${section.id}`}>{section[locale].title.replace(/^[\d০-৯]+\.\s*/, '')}</a></li>)}
              <li><a href="#contact">{bn ? 'যোগাযোগ' : 'Contact'}</a></li>
            </ol>
          </nav>
          <article className={styles.article} aria-label={bn ? 'ecomsbd গোপনীয়তা নীতি' : 'ecomsbd Privacy Policy'}>
            {policySections.map(section => (
              <section id={section.id} key={section.id}>
                <h2>{section[locale].title}</h2>
                {section[locale].paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
              </section>
            ))}
            <section id="contact">
              <h2>{bn ? '১৬. যোগাযোগ' : '16. Contact'}</h2>
              <p>{bn ? 'গোপনীয়তা, সহায়তা, তথ্য দেখা, সংশোধন বা মুছে ফেলার অনুরোধের জন্য ecomsbd-এর পরিচালকের সঙ্গে যোগাযোগ করুন: ' : 'For privacy questions, support, access, correction or deletion requests, contact the operator of ecomsbd at: '}<a href={`mailto:${privacyEmail}`}>{privacyEmail}</a>.</p>
              <p>{bn ? 'অ্যাকাউন্ট ও সংশ্লিষ্ট তথ্য মুছে ফেলার অনুরোধ করতে অ্যাপে সাইন-ইন করার প্রয়োজন নেই। নির্দেশনার জন্য দেখুন ' : 'You do not need to sign in to the app to request account and associated data deletion. See our '}<Link href={bn ? '/account-deletion/bn' : '/account-deletion'}>{bn ? 'অ্যাকাউন্ট মুছে ফেলার পৃষ্ঠা' : 'account deletion page'}</Link>.</p>
              <p>{bn ? 'সম্ভব হলে অ্যাকাউন্টের ইমেইল থেকে লিখুন এবং সংশ্লিষ্ট দোকানের নাম জানান। অনুরোধ যাচাই ও সমাধান করতে আমরা আপনার বার্তা ও যোগাযোগের তথ্য প্রক্রিয়া করি। Google (Gmail) আমাদের সহায়তার ইমেইলবক্স হোস্ট করে এবং আপনি পাঠানো ইমেইল প্রক্রিয়া করে। পাসওয়ার্ড, সাইন-ইন কোড বা সংযুক্ত সেবার গোপন তথ্য পাঠাবেন না।' : 'Where possible, write from your account email and identify the relevant shop. We process your message and contact details to verify and handle your request. Google (Gmail) hosts our support mailbox and processes the emails you send. Do not send passwords, sign-in codes or connected-service secrets.'}</p>
            </section>
          </article>
        </div>
      </main>
      <footer className={styles.footer}>
        <span>ecomsbd · scalemyprints.com</span>
        <Link href={bn ? '/privacy-policy/bn' : '/privacy-policy'}>{bn ? 'গোপনীয়তা নীতি' : 'Privacy Policy'}</Link>
        <Link href={bn ? '/account-deletion/bn' : '/account-deletion'}>{bn ? 'অ্যাকাউন্ট মুছে ফেলা' : 'Account deletion'}</Link>
        <a href="#policy">{bn ? 'ওপরে ফিরে যান' : 'Back to top'} ↑</a>
      </footer>
    </div>
  );
}
