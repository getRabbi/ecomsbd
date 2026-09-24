<?php
// ecomsbd Public API: a thin PHP client and webhook verifier (PHP 8.0+, ext-curl).
// Copy this file into your project. Laravel users: see sdk/examples/laravel/.

namespace Ecomsbd;

final class EcomsbdException extends \RuntimeException
{
    public function __construct(
        public readonly int $status,
        public readonly array $body,
        public readonly ?int $retryAfter = null
    ) {
        parent::__construct(sprintf('%d %s: %s', $status, $body['code'] ?? 'ERROR', $body['message_en'] ?? ''));
    }
}

final class Client
{
    public const ROUTES = [
        ['GET', '/me'],
        ['GET', '/orders'],
        ['GET', '/orders/{order_id}'],
        ['POST', '/orders'],
        ['POST', '/orders/{order_id}/status'],
        ['POST', '/sources/{source_id}/orders'],
        ['GET', '/customers'],
        ['POST', '/customers'],
        ['GET', '/products'],
        ['GET', '/inventory/{product_id}'],
        ['POST', '/inventory/{product_id}/adjustments'],
    ];

    private string $baseUrl;

    public function __construct(private string $apiKey, string $baseUrl, private int $timeout = 15)
    {
        if (!str_starts_with($apiKey, 'ec_live_')) {
            throw new \InvalidArgumentException('Use an ecomsbd API key (ec_live_...)');
        }
        $this->baseUrl = rtrim($baseUrl, '/');
    }

    /** Which key this is, its shop and scopes. A safe connection test. */
    public function me(): array
    {
        return $this->request('GET', '/me');
    }

    /** Custom Website: send an order. The same external order ID is never duplicated. */
    public function sendSourceOrder(string $sourceId, string $externalOrderId, array $payload, ?string $idempotencyKey = null): array
    {
        return $this->request('POST', "/sources/{$sourceId}/orders", [
            'external_order_id' => $externalOrderId,
            'payload' => $payload,
        ], [], $idempotencyKey ?? "order-{$externalOrderId}");
    }

    public function getOrder(string $orderId): array
    {
        return $this->request('GET', "/orders/{$orderId}");
    }

    /** CONFIRMED or CANCELLED. A booked order answers result=CONFLICT (HTTP 409). */
    public function setOrderStatus(string $orderId, string $status, ?string $reason = null): array
    {
        return $this->request('POST', "/orders/{$orderId}/status", [
            'status' => $status,
            'reason' => $reason,
        ], [], "status-{$orderId}-{$status}");
    }

    public function findProductsBySku(string $sku): array
    {
        return $this->request('GET', '/products', null, ['sku' => $sku])['items'];
    }

    public function getInventory(string $productId): array
    {
        return $this->request('GET', "/inventory/{$productId}");
    }

    /** Needs the inventory:write scope. */
    public function adjustInventory(string $productId, int $quantityDelta, string $idempotencyKey, string $reason = 'MANUAL_ADJUSTMENT', ?string $variantId = null): array
    {
        $body = ['quantity_delta' => $quantityDelta, 'reason' => $reason];
        if ($variantId !== null) {
            $body['variant_id'] = $variantId;
        }
        return $this->request('POST', "/inventory/{$productId}/adjustments", $body, [], $idempotencyKey);
    }

    public function request(string $method, string $path, ?array $body = null, array $query = [], ?string $idempotencyKey = null): array
    {
        $url = $this->baseUrl . $path . ($query ? '?' . http_build_query($query) : '');
        $headers = ['Authorization: Bearer ' . $this->apiKey, 'Accept: application/json'];
        if ($body !== null) {
            $headers[] = 'Content-Type: application/json';
        }
        if ($method === 'POST') {
            // Reuse the same key when retrying the same request; never for a new one.
            $headers[] = 'Idempotency-Key: ' . ($idempotencyKey ?? bin2hex(random_bytes(16)));
        }
        $retryAfter = null;
        $curl = curl_init($url);
        curl_setopt_array($curl, [
            CURLOPT_CUSTOMREQUEST => $method,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => $this->timeout,
            CURLOPT_HTTPHEADER => $headers,
            CURLOPT_HEADERFUNCTION => function ($curl, $line) use (&$retryAfter) {
                if (stripos($line, 'retry-after:') === 0) {
                    $retryAfter = (int) trim(substr($line, 12));
                }
                return strlen($line);
            },
        ]);
        if ($body !== null) {
            curl_setopt($curl, CURLOPT_POSTFIELDS, json_encode($body));
        }
        $raw = curl_exec($curl);
        if ($raw === false) {
            throw new \RuntimeException('Network error: ' . curl_error($curl));
        }
        $status = curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
        $data = $raw === '' ? [] : json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
        if ($status === 409 && ($data['result'] ?? null) === 'CONFLICT') {
            return $data;
        }
        if ($status >= 400) {
            throw new EcomsbdException($status, $data, $retryAfter);
        }
        return $data;
    }
}

final class Webhook
{
    public const SIGNATURE_HEADER = 'X-Ecomsbd-Signature';
    public const EVENT_ID_HEADER = 'X-Ecomsbd-Event-Id';
    public const TOLERANCE_SECONDS = 300;

    public static function sign(string $secret, string $rawBody, int $timestamp): string
    {
        return 't=' . $timestamp . ',v1=' . hash_hmac('sha256', $timestamp . '.' . $rawBody, $secret);
    }

    /** True only for an untampered body signed within the tolerance. Use the raw body. */
    public static function verify(string $secret, string $rawBody, ?string $header, int $tolerance = self::TOLERANCE_SECONDS, ?int $now = null): bool
    {
        if (!$header || !preg_match('/^t=(\d+),v1=([0-9a-f]{64})$/', $header, $m)) {
            return false;
        }
        $stamp = (int) $m[1];
        if (abs(($now ?? time()) - $stamp) > $tolerance) {
            return false;
        }
        return hash_equals(self::sign($secret, $rawBody, $stamp), $header);
    }
}
