
import io
import json
import logging
import oci

# --- Simplified Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

def handler(ctx, data: io.BytesIO = None):
    """A simple test function to fetch a secret and log its content."""
    
    # Get the secret OCID from the function's configuration
    secret_ocid = os.environ.get("DB_SECRET_OCID")
    if not secret_ocid:
        message = "CRITICAL: DB_SECRET_OCID environment variable not set."
        logger.critical(message)
        return {"status": "error", "message": message}

    logger.info(f"Attempting to fetch secret with OCID: {secret_ocid}")

    try:
        # Use Resource Principals for authentication
        signer = oci.auth.signers.get_resource_principals_signer()
        secrets_client = oci.secrets.SecretsClient(config={}, signer=signer)

        logger.info("OCI SecretsClient initialized. Fetching secret bundle...")

        # Explicitly ask for the LATEST version
        secret_bundle = secrets_client.get_secret_bundle(
            secret_id=secret_ocid,
            stage="LATEST"
        )

        logger.info("Successfully received a response from the Vault API.")

        # Extract the content
        secret_content = secret_bundle.data.secret_bundle_content.content

        # Log the raw content and its type and length for debugging
        logger.info(f"Type of secret_content: {type(secret_content)}")
        logger.info(f"Length of secret_content: {len(secret_content) if secret_content else 0}")
        logger.info(f"Raw secret_content received: '{secret_content}'")

        # Try to parse it as JSON
        try:
            parsed_json = json.loads(secret_content)
            logger.info("Successfully parsed secret content as JSON.")
            return {"status": "success", "secret_username": parsed_json.get("username")}
        except json.JSONDecodeError as e:
            logger.error(f"JSONDecodeError: Failed to parse the secret content. Error: {e}")
            return {"status": "error", "message": "JSONDecodeError", "raw_content": secret_content}

    except oci.exceptions.ServiceError as e:
        logger.critical(f"OCI ServiceError while fetching secret: {e}", exc_info=True)
        return {"status": "error", "message": f"OCI ServiceError: {e.status} {e.message}"}
    except Exception as e:
        logger.critical(f"An unexpected error occurred: {e}", exc_info=True)
        return {"status": "error", "message": f"Unexpected error: {str(e)}"}