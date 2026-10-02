"""Opaque id and token generation (ADR-003)."""

import secrets

HEX_BYTES = 8


def new_id(prefix):
    return "%s_%s" % (prefix, secrets.token_hex(HEX_BYTES))


def new_token():
    return secrets.token_urlsafe(32)