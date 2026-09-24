<?php
// Laravel controller that receives ecomsbd webhooks.
// routes/api.php:  Route::post('/ecomsbd/webhook', EcomsbdWebhookController::class);
// Exclude the route from CSRF, and set ECOMSBD_WEBHOOK_SECRET in .env.

namespace App\Http\Controllers;

use Ecomsbd\Webhook;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Cache;

require_once base_path('sdk/php/Ecomsbd.php'); // or wherever you copied Ecomsbd.php

class EcomsbdWebhookController
{
    public function __invoke(Request $request)
    {
        $raw = $request->getContent(); // the raw body, before json_decode
        if (!Webhook::verify(env('ECOMSBD_WEBHOOK_SECRET', ''), $raw, $request->header(Webhook::SIGNATURE_HEADER))) {
            return response('invalid signature', 401);
        }
        $event = json_decode($raw, true);
        // Deduplicate by event ID: a redelivered event is processed once.
        if (!Cache::add('ecomsbd-event:' . $event['id'], true, now()->addDays(3))) {
            return response('duplicate', 200);
        }
        match ($event['type']) {
            'tracking.assigned' => null, // save $event['data']['tracking_code'] on your order
            'order.delivered', 'order.returned', 'order.cancelled' => null, // update your order
            default => null,
        };
        return response('ok', 200); // 2xx only after the event is stored
    }
}
