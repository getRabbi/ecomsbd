"""Consignments: parcels and their per-item fulfilment lines.

Schema and state machine only. No courier is contacted from this package —
booking, status sync and BOOKING_UNKNOWN recovery are Phase C, and require
verified merchant documentation before a single HTTP call is written
(master spec sections 61, 140).

The tables exist now because ``consignment_items`` is the mapping master spec
section 72 requires for partial delivery, and everything downstream — partial
revenue, stock restore, correct profit, correct COD receivable — is built on it.
"""
