'use client';
import { useSession } from '@/lib/session';
import { useApi } from '@/lib/useApi';
import { ErrorState } from './ui';

export function ExternalRisk({ customerId }: { customerId: string }) {
  const { locale } = useSession();
  const result = useApi<{ status: string; message_en: string; message_bn: string }>(`/external-risk/customers/${customerId}`);
  return <section className="card" style={{ padding: 20 }}><h2>{locale === 'bn' ? 'বাইরের প্রোভাইডারের তথ্য' : 'External provider facts'}</h2>
    {result.error ? <ErrorState error={result.error} /> : <p>{result.data ? result.data[locale === 'bn' ? 'message_bn' : 'message_en'] : locale === 'bn' ? 'লোড হচ্ছে…' : 'Loading…'}</p>}
  </section>;
}
