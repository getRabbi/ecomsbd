"""Billing: subscriptions, providers, verification and dunning.

Master spec sections 27, 90–93. The rule the whole module is built around is
section 90's last line: **never grant a paid entitlement solely from a client
callback.** A purchase token is a claim; a provider response is evidence.
"""
