"""Platform SMS gateway accounts (V4 WP7.1).

One provider so far, Alpha SMS (``sms.net.bd``), behind the existing
:class:`~app.notifications.transport.SmsTransport` port. Credentials are
entered in the admin console, sealed with the credential vault and never
returned; nothing here reads a key from the environment.
"""
