"""Mailpool client (cold-email domains, DNS, mailboxes, spam checks, warmup)."""

from .client import (MailpoolClient, MailpoolDnsWriteError, is_secret_key,
                     project_mailbox, strip_secrets)

__all__ = ["MailpoolClient", "MailpoolDnsWriteError", "is_secret_key",
           "project_mailbox", "strip_secrets"]
