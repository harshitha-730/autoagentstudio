import base64
import hashlib
import hmac
import os


PBKDF2_ITERATIONS = int(os.getenv("PASSWORD_HASH_ITERATIONS", "390000"))
SALT_SIZE = 16
HASH_NAME = "sha256"
DERIVED_KEY_LENGTH = 32


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("Password cannot be empty.")

    salt = os.urandom(SALT_SIZE)
    derived_key = hashlib.pbkdf2_hmac(
        HASH_NAME,
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=DERIVED_KEY_LENGTH,
    )
    payload = salt + derived_key
    return base64.b64encode(payload).decode("utf-8")


def verify_password(password: str, stored_hash: str) -> bool:
    if not password or not stored_hash:
        return False

    try:
        decoded = base64.b64decode(stored_hash.encode("utf-8"))
        salt = decoded[:SALT_SIZE]
        expected_key = decoded[SALT_SIZE:]
    except Exception:
        return False

    test_key = hashlib.pbkdf2_hmac(
        HASH_NAME,
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=len(expected_key),
    )

    return hmac.compare_digest(expected_key, test_key)
