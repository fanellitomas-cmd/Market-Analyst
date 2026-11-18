"""Genera el hash bcrypt para APP_PASSWORD_HASH y un JWT_SECRET aleatorio.

Uso (desde backend/):  python scripts/hash_password.py
"""

import getpass
import secrets

import bcrypt


def main() -> None:
    password = getpass.getpass("Contraseña nueva: ")
    if len(password) < 10:
        raise SystemExit("Usá al menos 10 caracteres.")
    if password != getpass.getpass("Repetila: "):
        raise SystemExit("Las contraseñas no coinciden.")
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    print("\nPegá estas líneas en backend/.env:\n")
    # Comillas simples: el hash contiene '$' y algunos cargadores de .env lo interpretan.
    print(f"APP_PASSWORD_HASH='{hashed}'")
    print(f"JWT_SECRET='{secrets.token_urlsafe(48)}'")


if __name__ == "__main__":
    main()
