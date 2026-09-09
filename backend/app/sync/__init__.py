"""Offline mutation sync.

Master spec section 38: ``POST /v1/sync/mutations`` and
``GET /v1/sync/changes?cursor=…``. The server returns accepted mutations,
conflicts, changed entities and the next cursor.
"""
