"""
test_liboqs.py
---------------
Standalone sanity check for the liboqs-python installation, independent of
the FastAPI app or the agent pipeline. Run this FIRST after `docker compose
up --build` finishes, to confirm ML-KEM-512 actually works before you try
the full Scanner -> Refactor -> Tester flow.

Usage (from inside the running backend container):
    docker compose exec backend python test_liboqs.py

Usage (locally, if you have liboqs-python installed in a venv):
    python test_liboqs.py

Expected output ends with "ALL CHECKS PASSED".
"""

import sys


def main() -> int:
    print("=" * 60)
    print("PQC-Guard AI — liboqs-python sanity check")
    print("=" * 60)

    # --- Step 1: import ---
    try:
        import oqs
    except ImportError as e:
        print(f"[FAIL] Could not import oqs: {e}")
        print("       -> liboqs-python is not installed in this environment.")
        return 1
    print("[OK]   'import oqs' succeeded.")

    # --- Step 2: confirm ML-KEM-512 is available ---
    try:
        enabled_kems = oqs.get_enabled_kem_mechanisms()
    except Exception as e:
        print(f"[FAIL] Could not list enabled KEM mechanisms: {e}")
        return 1

    if "ML-KEM-512" not in enabled_kems:
        print("[FAIL] 'ML-KEM-512' is not in the enabled KEM mechanisms list.")
        print(f"       Available mechanisms: {enabled_kems}")
        return 1
    print("[OK]   'ML-KEM-512' is available.")

    # --- Step 3: full round trip — keypair, encapsulate, decapsulate ---
    try:
        with oqs.KeyEncapsulation("ML-KEM-512") as kem_alice:
            public_key = kem_alice.generate_keypair()
            secret_key = kem_alice.export_secret_key()
            print(f"[OK]   Generated keypair (public key: {len(public_key)} bytes, "
                  f"secret key: {len(secret_key)} bytes).")

            # Someone else (Bob) encapsulates a shared secret using Alice's public key
            with oqs.KeyEncapsulation("ML-KEM-512") as kem_bob:
                ciphertext, shared_secret_bob = kem_bob.encap_secret(public_key)
            print(f"[OK]   Encapsulated shared secret (ciphertext: {len(ciphertext)} bytes, "
                  f"shared secret: {len(shared_secret_bob)} bytes).")

            # Alice decapsulates using her secret key to recover the same shared secret
            shared_secret_alice = kem_alice.decap_secret(ciphertext)
            print(f"[OK]   Decapsulated shared secret ({len(shared_secret_alice)} bytes).")

    except Exception as e:
        print(f"[FAIL] Round-trip KEM operation raised an exception: {e}")
        return 1

    # --- Step 4: verify both sides derived the SAME shared secret ---
    if shared_secret_alice != shared_secret_bob:
        print("[FAIL] Shared secrets do NOT match between the two parties!")
        return 1
    print("[OK]   Shared secrets match between both parties — key exchange is correct.")

    print("=" * 60)
    print("ALL CHECKS PASSED — liboqs-python + ML-KEM-512 is working correctly.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
