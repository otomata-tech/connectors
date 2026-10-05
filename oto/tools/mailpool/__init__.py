"""Mailpool client (cold-email domains, DNS, mailboxes, spam checks, warmup)."""

from .client import MailpoolClient, strip_secrets

__all__ = ["MailpoolClient", "strip_secrets"]
