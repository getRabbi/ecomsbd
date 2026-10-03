import type { Metadata } from 'next';
import Link from 'next/link';

import shared from '../privacy-policy/privacy.module.css';
import styles from './ecomsbd.module.css';

export const metadata: Metadata = {
  title: 'ecomsbd | Your complete seller operations toolkit',
  description: 'Manage orders, customers, couriers, COD payments and sales-channel automation from one place.',
  alternates: { canonical: 'https://scalemyprints.com/ecomsbd' },
  robots: { index: true, follow: true },
};

// Only what ships today. Each line here is a claim to App Review and to sellers.
const features = [
  { title: 'Order Management', body: 'Create, confirm and update orders, and follow each one from new to delivered.' },
  { title: 'Customer Tracking', body: 'Keep each customer’s details and order history together, so you know who you are shipping to.' },
  { title: 'Courier Connections', body: 'Connect your supported courier accounts to book parcels and follow delivery status without retyping order details.' },
  { title: 'COD & Money Tracking', body: 'Track cash-on-delivery amounts from delivery to payout and reconcile them against your orders.' },
  { title: 'WooCommerce Integration', body: 'Connect your WordPress + WooCommerce store and new orders arrive in ecomsbd automatically.' },
  { title: 'Custom Website Integration', body: 'Your own website can send orders to ecomsbd with a secure API key.' },
  { title: 'Messenger & WhatsApp Chat-to-Order Automation', body: 'Capture customer order information from connected Messenger and WhatsApp Business conversations. Captured details become draft orders that you review before confirming.' },
  { title: 'Seller Insights', body: 'See how your orders, deliveries and revenue are trending, so you can decide what to do next.' },
];

const flow = ['Order captured', 'Confirmed', 'Booked with courier', 'Delivered', 'COD reconciled'];

/** The App Store Marketing URL. Public and static: no session, no API calls. */
export default function MarketingPage() {
  return (
    <div className={shared.page} lang="en">
      <a className={shared.skip} href="#features">Skip to features</a>
      <header className={shared.header}>
        <Link className={shared.brand} href="/ecomsbd">ecomsbd<span>Seller operations, simply</span></Link>
        <nav aria-label="ecomsbd" className={styles.nav}>
          <Link href="/ecomsbd-support">Support</Link>
        </nav>
      </header>
      <main>
        <section className={styles.hero} aria-labelledby="hero-title">
          <div className={styles.heroText}>
            <p className={shared.eyebrow}>FOR ONLINE SELLERS IN BANGLADESH</p>
            <h1 id="hero-title">ecomsbd<span>Your complete seller operations toolkit</span></h1>
            <p className={styles.lead}>Manage orders, customers, couriers, COD payments and sales-channel automation from one place.</p>
            <div className={styles.actions}>
              <a className={styles.primary} href="#features">See what it does</a>
              <Link className={styles.secondary} href="/ecomsbd-support">Get support</Link>
            </div>
          </div>
          <ol className={styles.flow} aria-label="How an order moves through ecomsbd">
            {flow.map((step, index) => (
              <li key={step}><span aria-hidden="true">{index + 1}</span>{step}</li>
            ))}
          </ol>
        </section>
        <section id="features" className={styles.features} aria-labelledby="features-title">
          <h2 id="features-title">Everything between the order and the payout</h2>
          <ul className={styles.grid}>
            {features.map(feature => (
              <li key={feature.title} className={styles.card}>
                <h3>{feature.title}</h3>
                <p>{feature.body}</p>
              </li>
            ))}
          </ul>
        </section>
        <section className={styles.help} aria-labelledby="help-title">
          <h2 id="help-title">Help and your data</h2>
          <p>Questions about your account, orders or connections? <Link href="/ecomsbd-support">Visit Support</Link>. Read our <Link href="/privacy-policy">Privacy Policy</Link>, or see how to <Link href="/account-deletion">delete your account</Link>.</p>
        </section>
      </main>
      <footer className={shared.footer}>
        <span>ecomsbd · scalemyprints.com</span>
        <Link href="/ecomsbd-support">Support</Link>
        <Link href="/privacy-policy">Privacy Policy</Link>
        <Link href="/account-deletion">Account deletion</Link>
      </footer>
    </div>
  );
}
