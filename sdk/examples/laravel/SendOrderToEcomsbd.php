<?php
// Laravel: send a checkout to ecomsbd from a queued job or an order observer.
// .env: ECOMSBD_API_KEY, ECOMSBD_API_BASE (https://YOUR-API-HOST/public/v1), ECOMSBD_SOURCE_ID

namespace App\Jobs;

use Ecomsbd\Client;
use Ecomsbd\EcomsbdException;
use Illuminate\Contracts\Queue\ShouldQueue;

require_once base_path('sdk/php/Ecomsbd.php');

class SendOrderToEcomsbd implements ShouldQueue
{
    public int $tries = 5;

    public function __construct(private array $order)
    {
    }

    public function handle(): void
    {
        $client = new Client(env('ECOMSBD_API_KEY'), env('ECOMSBD_API_BASE'));
        try {
            // The shop's own order number is the identity: retries never duplicate.
            $client->sendSourceOrder(env('ECOMSBD_SOURCE_ID'), $this->order['number'], [
                'phone' => $this->order['phone'],
                'customer_name' => $this->order['name'],
                'address' => $this->order['address'],
                'district' => $this->order['district'],
                'items' => array_map(fn ($line) => [
                    'name' => $line['title'] . ' (' . $line['sku'] . ')',
                    'quantity' => $line['quantity'],
                    'unit_price_paisa' => $line['price_paisa'],
                ], $this->order['lines']),
                'cod_amount_paisa' => $this->order['cod_paisa'],
            ]);
        } catch (EcomsbdException $e) {
            if ($e->status === 429) {
                $this->release($e->retryAfter ?? 60);
                return;
            }
            throw $e;
        }
    }
}
