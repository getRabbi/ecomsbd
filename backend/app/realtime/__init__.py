"""Invalidation-only realtime: committed changes tell clients what to refetch.

See :mod:`app.realtime.publisher` (after-commit capture), :mod:`app.realtime.hub`
(the sockets a process holds) and :mod:`app.api.v1.realtime` (the socket).
"""
