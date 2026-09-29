import Link from 'next/link';

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
            </ol>
          </nav>
          <article className={styles.article} aria-label={bn ? 'ecomsbd গোপনীয়তা নীতি' : 'ecomsbd Privacy Policy'}>
            {policySections.map(section => (
              <section id={section.id} key={section.id}>
                <h2>{section[locale].title}</h2>
                {section[locale].paragraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
              </section>
            ))}
          </article>
        </div>
      </main>
      <footer className={styles.footer}>
        <span>ecomsbd · scalemyprints.com</span>
        <Link href={bn ? '/privacy-policy/bn' : '/privacy-policy'}>{bn ? 'গোপনীয়তা নীতি' : 'Privacy Policy'}</Link>
        <a href="#policy">{bn ? 'ওপরে ফিরে যান' : 'Back to top'} ↑</a>
      </footer>
    </div>
  );
}
