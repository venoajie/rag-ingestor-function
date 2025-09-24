
import io
import json
import logging
import oci
import os
import base64 # <-- 1. IMPORT THE LIBRARY

# --- Simplified Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

def handler(ctx, data: io.BytesIO = None):
    """A simple test function to fetch a secret and log its content."""
    
    secret_ocid = os.environ.get("DB_SECRET_OCID")
    if not secret_ocid:
        message = "CRITICAL: DB_SECRET_OCID environment variable not set."
        logger.critical(message)
        return {"status": "error", "message": message}

    logger.info(f"Attempting to fetch secret with OCID: {secret_ocid}")

    try:
        signer = oci.auth.signers.get_resource_principals_signer()
        secrets_client = oci.secrets.SecretsClient(config={}, signer=signer)

        logger.info("OCI SecretsClient initialized. Fetching secret bundle...")

        secret_bundle = secrets_client.get_secret_bundle(
            secret_id=secret_ocid,
            stage="LATEST"
        )

        logger.info("Successfully received a response from the Vault API.")

        # Extract the BASE64 content
        secret_content_base64 = secret_bundle.data.secret_bundle_content.content

        # <-- 2. DECODE THE SECRET FROM BASE64
        decoded_bytes = base64.b64decode(secret_content_base64)
        secret_content_decoded = decoded_bytes.decode('utf-8')

        # Log the DECODED content for debugging
        logger.info(f"Length of DECODED secret_content: {len(secret_content_decoded)}")
        logger.info(f"Raw DECODED secret_content received: '{secret_content_decoded}'")

        # Try to parse the DECODED string as JSON
        try:
            parsed_json = json.loads(secret_content_decoded)
            logger.info("Successfully parsed secret content as JSON.")
            return {"status": "success", "secret_username": parsed_json.get("username")}
        except json.JSONDecodeError as e:
            logger.error(f"JSONDecodeError: Failed to parse the DECODED secret content. Error: {e}")
            return {"status": "error", "message": "JSONDecodeError", "raw_content": secret_content_decoded}

    except oci.exceptions.ServiceError as e:
        logger.critical(f"OCI ServiceError while fetching secret: {e}", exc_info=True)
        return {"status": "error", "message": f"OCI ServiceError: {e.status} {e.message}"}
    except Exception as e:
        logger.critical(f"An unexpected error occurred: {e}", exc_info=True)
        return {"status": "error", "message": f"Unexpected error: {str(e)}"}