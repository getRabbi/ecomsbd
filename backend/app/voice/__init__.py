"""Voice confirmation calls (V4 Phase 3).

Provider-neutral like SMS and couriers (rule L8): :mod:`app.voice.transport` is
the port, :mod:`app.voice.manifest` says what a provider has been verified to do,
and :mod:`app.voice.connections` holds the platform's provider account. No
provider adapter exists until an operator-supplied contract does
(``VOICE_PROVIDER_CONTRACT_REQUIRED``).
"""
