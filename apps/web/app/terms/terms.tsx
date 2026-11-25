import Link from 'next/link';
import { privacyEmail } from '@/lib/legal';

import { StoreLinks } from '../ecomsbd/store-links';
import type { PolicyLocale } from '../privacy-policy/content';
import styles from '../privacy-policy/privacy.module.css';
import { termsHeading, termsSections, termsUpdated } from './content';

export function TermsOfService({ locale }: { locale: PolicyLocale }) {
  const bn = locale === 'bn';
  const head = termsHeading[locale];
  return (
    <div className={styles.page} lang={locale}>
      <a className={styles.skip} href="#terms">{bn ? 'শর্তাবলিতে যান' : 'Skip to terms'}</a>
      <header className={styles.header}>
        <Link className={styles.brand} href="/">ecomsbd<span>{bn ? 'বিক্রেতার কাজ, সহজভাবে' : 'Seller operations, simply'}</span></Link>
        <nav aria-label={bn ? 'শর্তাবলির ভাষা' : 'Terms language'} className={styles.languages}>
          <Link href="/terms" hrefLang="en" lang="en" aria-current={!bn ? 'page' : undefined}>English</Link>
          <Link href="/terms/bn" hrefLang="bn" lang="bn" aria-current={bn ? 'page' : undefined}>বাংলা</Link>
        </nav>
      </header>
      <main id="terms" className={styles.main}>
        <div className={styles.intro}>
          <p className={styles.eyebrow}>ECOMSBD · {head.eyebrow}</p>
          <h1>{head.title}</h1>
          <p className={styles.lead}>{head.lead}</p>
          <p className={styles.date}>{bn ? 'সর্বশেষ হালনাগাদ: ' : 'Last updated: '}<time dateTime={termsUpdated}>{head.date}</time></p>
        </div>
        <div className={styles.layout}>
          <nav className={styles.contents} aria-label={bn ? 'সূচিপত্র' : 'On this page'}>
            <p>{bn ? 'এই পৃষ্ঠায়' : 'On this page'}</p>
            <ol>
              {termsSections.map(section => <li key={section.id}><a href={`#${section.id}`}>{section[locale].title.replace(/^[\d০-৯]+\.\s*/, '')}</a></li>)}
              <li><a href="#contact">{bn ? 'যোগাযোগ' : 'Contact'}</a></li>
            </ol>
          </nav>
          <article className={styles.article} aria-label={bn ? 'ecomsbd সেবার শর্তাবলি' : 'ecomsbd Terms of Service'}>
            {termsSections.map(section => (
              <section id={section.id} key={section.id}>
                <h2>{section[locale].title}</h2>
                {section[locale].paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
              </section>
            ))}
            <section id="contact">
              <h2>{bn ? '১৫. যোগাযোগ' : '15. Contact'}</h2>
              <p>{bn ? 'এই শর্তাবলি নিয়ে প্রশ্ন থাকলে ecomsbd-এর পরিচালকের সঙ্গে যোগাযোগ করুন: ' : 'For questions about these terms, contact the operator of ecomsbd at: '}<a href={`mailto:${privacyEmail}`}>{privacyEmail}</a>.</p>
              <p>{bn ? 'আপনার তথ্য কীভাবে ব্যবহার হয়, তা জানতে পড়ুন ' : 'How your information is handled is explained in our '}<Link href={bn ? '/privacy-policy/bn' : '/privacy-policy'}>{bn ? 'গোপনীয়তা নীতি' : 'Privacy Policy'}</Link>{bn ? '। অ্যাকাউন্ট মুছে ফেলতে দেখুন ' : '. To delete your account, see the '}<Link href={bn ? '/account-deletion/bn' : '/account-deletion'}>{bn ? 'অ্যাকাউন্ট মুছে ফেলার পৃষ্ঠা' : 'account deletion page'}</Link>.</p>
            </section>
          </article>
        </div>
      </main>
      <footer className={styles.footer}>
        <span>ecomsbd · ecomsbd.com</span>
        <Link href={bn ? '/terms/bn' : '/terms'}>{head.title}</Link>
        <Link href={bn ? '/privacy-policy/bn' : '/privacy-policy'}>{bn ? 'গোপনীয়তা নীতি' : 'Privacy Policy'}</Link>
        <Link href={bn ? '/account-deletion/bn' : '/account-deletion'}>{bn ? 'অ্যাকাউন্ট মুছে ফেলা' : 'Account deletion'}</Link>
        <a href="#terms">{bn ? 'ওপরে ফিরে যান' : 'Back to top'} ↑</a>
        <StoreLinks locale={locale} compact className={styles.footerApps} />
      </footer>
    </div>
  );
}
