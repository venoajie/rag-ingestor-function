
import oci
import os
from sqlalchemy import create_engine

# --- Configuration ---
# Use the same environment variables your function uses
SECRET_OCID = "ocid1.vaultsecret.oc1.eu-frankfurt-1.amaaaaaaaenu5lyas5scqzuhlws7pzsg6jxsmcbybu2uecizvqs5p2ghbuda"

print("--- Starting Local Database Connection Test ---")

try:
    # Step 1: Authenticate using your local ~/.oci/config file
    print("Authenticating with local OCI config...")
    config = oci.config.from_file()
    signer = oci.signer.Signer(
        tenancy=config["tenancy"],
        user=config["user"],
        fingerprint=config["fingerprint"],
        private_key_file_location=config["key_file"],
        pass_phrase=oci.config.get_config_value_or_default(config, "pass_phrase"),
    )
    secrets_client = oci.secrets.SecretsClient(config={}, signer=signer)
    print("Authentication successful.")

    # Step 2: Fetch the secret from OCI Vault
    print(f"Fetching secret: {SECRET_OCID}")
    secret_bundle = secrets_client.get_secret_bundle(secret_id=SECRET_OCID)
    secret_content = secret_bundle.data.secret_bundle_content.content
    print("Secret fetched successfully.")

    # Step 3: THIS IS THE BLACK BOX RECORDER. Print the raw secret.
    # The repr() function will explicitly show hidden characters like '\n'
    print(f"RAW secret content (length {len(secret_content)}): {repr(secret_content)}")

    # Step 4: Clean the secret using .strip()
    db_connection_string_cleaned = secret_content.strip()
    print(f"CLEANED secret content (length {len(db_connection_string_cleaned)}): {repr(db_connection_string_cleaned)}")

    # Step 5: IMPORTANT - Modify the string for the SSH tunnel
    # We replace the private IP with 'localhost' because of our tunnel
    local_db_connection_string = db_connection_string_cleaned.replace("10.0.0.146", "localhost")
    print(f"Final connection string for local test: '{local_db_connection_string}'")

    # Step 6: Attempt to create the engine and connect
    print("Attempting to create SQLAlchemy engine...")
    engine = create_engine(local_db_connection_string)
    
    print("Engine created. Attempting to connect...")
    with engine.connect() as connection:
        print("✅✅✅ SUCCESS! Database connection established. ✅✅✅")

except Exception as e:
    print(f"\n❌❌❌ TEST FAILED ❌❌❌")
    print(f"Error Type: {type(e).__name__}")
    print(f"Error Details: {e}")