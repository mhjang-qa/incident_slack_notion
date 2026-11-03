"""Only authenticated ciphertext, never Slack plaintext, enters the Actions cache."""
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AAD = b"deployment-reporter/sqlite-cache/v1"


def cipher(secret):
    if not secret:
        raise ValueError("A high entropy integration secret is required for state encryption")
    return AESGCM(hashlib.sha256(AAD + b"\0" + secret.encode()).digest())


def restore_state(encrypted, database, secret):
    source = Path(encrypted)
    if not source.exists():
        return False
    data = source.read_bytes()
    plaintext = cipher(secret).decrypt(data[:12], data[12:], AAD)
    target = Path(database)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Authentication failure never overwrites an existing database.
    temporary = target.with_suffix(".restoring")
    temporary.write_bytes(plaintext)
    temporary.chmod(0o600)
    temporary.replace(target)
    return True


def save_state(database, encrypted, secret):
    target = Path(encrypted)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        backup = Path(temp) / "backup.db"
        with sqlite3.connect(database) as source, sqlite3.connect(backup) as destination:
            source.backup(destination)
        nonce = os.urandom(12)
        ciphertext = nonce + cipher(secret).encrypt(nonce, backup.read_bytes(), AAD)
    temporary = target.with_suffix(".saving")
    temporary.write_bytes(ciphertext)
    temporary.chmod(0o600)
    temporary.replace(target)
