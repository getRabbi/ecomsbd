<?php
// php sdk/php/selftest.php  — checks the PHP helper against the API's own signing.
require __DIR__ . '/Ecomsbd.php';

use Ecomsbd\Client;
use Ecomsbd\Webhook;

function check(bool $ok, string $what): void
{
    if (!$ok) {
        fwrite(STDERR, "FAIL: $what\n");
        exit(1);
    }
}

$secret = 'placeholder-signing-secret';
$body = '{"id":"e1","type":"order.delivered"}';
// Produced by the API's signature() for the same inputs (see backend tests).
$expected = 't=1000,v1=af66d2bcbfc2077710d11feb3d1614439fdc6e2bd0b3d3cc75e568991a37d6bf';

check(Webhook::sign($secret, $body, 1000) === $expected, 'signature matches the API');
check(Webhook::verify($secret, $body, $expected, 300, 1000), 'valid signature accepted');
check(!Webhook::verify($secret, $body . ' ', $expected, 300, 1000), 'tampered body rejected');
check(!Webhook::verify('wrong', $body, $expected, 300, 1000), 'wrong secret rejected');
check(!Webhook::verify($secret, $body, $expected, 300, 1301), 'replayed timestamp rejected');
check(!Webhook::verify($secret, $body, null), 'missing header rejected');
check(count(Client::ROUTES) === 11, 'route list present');
try {
    new Client('sk_other', 'https://x.test');
    check(false, 'foreign key refused');
} catch (InvalidArgumentException) {
}
echo "PHP SDK self-test passed\n";
