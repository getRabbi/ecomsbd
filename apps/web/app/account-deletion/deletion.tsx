import Link from 'next/link';

import { deletionEmailLink, privacyEmail } from '@/lib/legal';
import { updated, type PolicyLocale } from '../privacy-policy/content';
import styles from '../privacy-policy/privacy.module.css';
import { deletionContent } from './content';

export function AccountDeletion({ locale }: { locale: PolicyLocale }) {
  const bn = locale === 'bn';
  const text = deletionContent[locale];
  return (
    <div className={styles.page} lang={locale}>
      <a className={styles.skip} href="#request-deletion">{bn ? 'অনুরোধের নির্দেশনায় যান' : 'Skip to deletion instructions'}</a>
      <header className={styles.header}>
        <Link className={styles.brand} href="/">ecomsbd<span>{bn ? 'বিক্রেতার কাজ, সহজভাবে' : 'Seller operations, simply'}</span></Link>
        <nav aria-label={bn ? 'পৃষ্ঠার ভাষা' : 'Page language'} className={styles.languages}>
          <Link href="/account-deletion" hrefLang="en" lang="en" aria-current={!bn ? 'page' : undefined}>English</Link>
          <Link href="/account-deletion/bn" hrefLang="bn" lang="bn" aria-current={bn ? 'page' : undefined}>বাংলা</Link>
        </nav>
      </header>
      <main className={`${styles.main} ${styles.deletionMain}`}>
        <div className={styles.intro}>
          <p className={styles.eyebrow}>ECOMSBD · {bn ? 'অ্যাকাউন্ট ও তথ্য' : 'ACCOUNT & DATA'}</p>
          <h1>{text.title}</h1>
          <p className={styles.lead}>{text.intro}</p>
          <p className={styles.date}><time dateTime={updated}>{text.lastUpdated}</time></p>
        </div>
        <article className={styles.article}>
          <section id="request-deletion">
            <h2>{text.requestTitle}</h2>
            <p>{text.requestIntro}</p>
            <div className={styles.requestContact}>
              <a href={deletionEmailLink}>{privacyEmail}</a>
              <a className={styles.requestButton} href={deletionEmailLink}>{text.emailAction}</a>
            </div>
            <p>{text.emailHint}</p>
            <ol className={styles.steps}>{text.steps.map(step => <li key={step}>{step}</li>)}</ol>
            <p>{text.review}</p>
          </section>
          <section id="in-app-deletion">
            <h2>{text.appTitle}</h2>
            {text.appParagraphs.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
          </section>
          <section id="deleted-data">
            <h2>{text.removedTitle}</h2>
            <ul className={styles.steps}>{text.removed.map(item => <li key={item}>{item}</li>)}</ul>
          </section>
          <section id="retained-data">
            <h2>{text.retainedTitle}</h2>
            {text.retained.map(paragraph => <p key={paragraph}>{paragraph}</p>)}
          </section>
          <section id="seller-customers">
            <h2>{text.customerTitle}</h2>
            <p>{text.customer}</p>
            <p>{text.policyIntro}<Link href={bn ? '/privacy-policy/bn' : '/privacy-policy'}>{text.policyLabel}</Link>.</p>
          </section>
        </article>
      </main>
      <footer className={styles.footer}>
        <span>ecomsbd · scalemyprints.com</span>
        <Link href={bn ? '/privacy-policy/bn' : '/privacy-policy'}>{bn ? 'গোপনীয়তা নীতি' : 'Privacy Policy'}</Link>
        <Link href={bn ? '/account-deletion/bn' : '/account-deletion'}>{bn ? 'অ্যাকাউন্ট মুছে ফেলা' : 'Account deletion'}</Link>
      </footer>
    </div>
  );
}
