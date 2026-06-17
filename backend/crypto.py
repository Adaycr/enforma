"""
Crypto Manager - AES-256 encryption for credentials and sensitive data.
Key is derived from machine-specific data + stored salt.
"""
import os
import base64
import json
import hashlib
from pathlib import Path
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes


class CryptoManager:
    def __init__(self, key_file: Path):
        self.key_file = key_file
        self._fernet = None
        self._ensure_key()

    def _get_machine_id(self) -> str:
        """Get a machine-specific identifier."""
        sources = []
        
        # Try /etc/machine-id (Linux)
        try:
            with open("/etc/machine-id", "r") as f:
                sources.append(f.read().strip())
        except Exception:
            pass
        
        # Try hostname
        try:
            import socket
            sources.append(socket.gethostname())
        except Exception:
            pass
        
        # Try username
        try:
            import getpass
            sources.append(getpass.getuser())
        except Exception:
            pass
        
        combined = "|".join(sources) if sources else "sports-dashboard-default"
        return hashlib.sha256(combined.encode()).hexdigest()

    def _ensure_key(self):
        """Load or generate encryption key."""
        if self.key_file.exists():
            with open(self.key_file, "rb") as f:
                key_data = json.loads(f.read())
            salt = base64.b64decode(key_data["salt"])
        else:
            # Generate new salt
            salt = os.urandom(16)
            machine_id = self._get_machine_id()
            
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100_000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(machine_id.encode()))
            
            key_data = {
                "salt": base64.b64encode(salt).decode(),
                "key": key.decode()
            }
            
            # Save key file with restricted permissions
            self.key_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.key_file, "wb") as f:
                f.write(json.dumps(key_data).encode())
            os.chmod(self.key_file, 0o600)
        
        # Re-derive key from machine_id + salt for security
        machine_id = self._get_machine_id()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(machine_id.encode()))
        self._fernet = Fernet(key)

    def encrypt(self, data: str) -> str:
        """Encrypt a string and return base64-encoded ciphertext."""
        return self._fernet.encrypt(data.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a base64-encoded ciphertext and return plaintext."""
        return self._fernet.decrypt(ciphertext.encode()).decode()

    def encrypt_dict(self, d: dict) -> str:
        """Encrypt a dictionary as JSON."""
        return self.encrypt(json.dumps(d))

    def decrypt_dict(self, ciphertext: str) -> dict:
        """Decrypt and parse a JSON dictionary."""
        return json.loads(self.decrypt(ciphertext))
