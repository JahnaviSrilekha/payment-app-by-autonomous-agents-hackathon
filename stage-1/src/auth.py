"""Signup, login, bearer-token authentication and GET /me (spec sections 4, 6).

Password hashing happens strictly outside STATE_LOCK (design.md section 3, ADR-002):
signup hashes before taking the lock; login takes the lock only to copy the stored hash
out, verifies outside the lock, then takes the lock again to mint the token.
"""

import hashlib
import hmac
import secrets

import errors
import ids
import state as state_mod

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16
PASSWORD_MIN = 8


def hash_password(password):
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN)
    return {
        "algorithm": "scrypt",
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
        "salt": salt.hex(),
        "hash": derived.hex(),
    }


def verify_password(password, record):
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(record["salt"]),
        n=record["n"],
        r=record["r"],
        p=record["p"],
        dklen=len(bytes.fromhex(record["hash"])),
    )
    return hmac.compare_digest(derived, bytes.fromhex(record["hash"]))


def authenticate(headers):
    """Resolve the bearer token to a user. Call while holding STATE_LOCK."""
    header = headers.get("Authorization")
    if header is None:
        raise errors.unauthenticated()
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise errors.unauthenticated()
    token = parts[1].strip()
    if not token:
        raise errors.unauthenticated()
    service = state_mod.get()
    user_id = service["tokens"].get(token)
    if user_id is None or user_id not in service["users"]:
        raise errors.unauthenticated()
    return service["users"][user_id]


def signup_commit(email, password_record, display_name, handle):
    """Create the account and mint a token. Call while holding STATE_LOCK; the password
    must already be hashed (outside the lock)."""
    service = state_mod.get()
    if email in service["emails"]:
        raise errors.email_taken()
    if handle in service["handles"]:
        raise errors.handle_taken()
    user_id = ids.new_id("u")
    service["users"][user_id] = {
        "id": user_id,
        "email": email,
        "password_hash": password_record,
        "display_name": display_name,
        "handle": handle,
        "balance": 0,
    }
    service["handles"][handle] = user_id
    service["emails"][email] = user_id
    token = mint_token(user_id)
    return {
        "user_id": user_id,
        "display_name": display_name,
        "token": token,
    }


def login_copy_hash(email):
    """Copy the stored password hash out. Call while holding STATE_LOCK.
    Returns (user_id, record); (None, None) for an unknown email."""
    service = state_mod.get()
    user_id = service["emails"].get(email)
    if user_id is None or user_id not in service["users"]:
        return None, None
    return user_id, service["users"][user_id]["password_hash"]


def mint_token(user_id):
    """Mint and store a new bearer token. Call while holding STATE_LOCK."""
    service = state_mod.get()
    token = ids.new_token()
    service["tokens"][token] = user_id
    return token


def login_finish(user_id):
    """Mint the token after out-of-lock verification. Call while holding STATE_LOCK."""
    service = state_mod.get()
    user = service["users"].get(user_id)
    if user is None:
        raise errors.unauthenticated()
    token = mint_token(user_id)
    return {
        "user_id": user["id"],
        "display_name": user["display_name"],
        "token": token,
    }


def me_response(user):
    service = state_mod.get()
    return {
        "user_id": user["id"],
        "display_name": user["display_name"],
        "handle": user["handle"],
        "balance": user["balance"],
        "currency": service["currency"],
        "minor_units": service["minor_units"],
    }