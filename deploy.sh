
```bash
#!/bin/bash
# ==============================================================================
# RAG Ingestor - Robust Deployment Script v2.0
#
# This script is designed to be idempotent and self-healing. It will:
#   1. Verify prerequisites (like Docker login).
#   2. Force-recreate the Fn CLI context to prevent corruption.
#   3. Deploy the function and apply its configuration.
# ==============================================================================
set -e # Exit immediately if a command exits with a non-zero status.

# --- ACTION REQUIRED: Fill in these 3 values ---
export OCI_USERNAME="<your-oci-username-for-docker-login>" # e.g., 'firstname.lastname@example.com' or 'identity/user.name'
export TENANCY_OCID="<PASTE_YOUR_TENANCY_OCID_HERE>"
# Note: The Auth Token is now used in a manual step before running the script.

# --- Confirmed Values (No changes needed) ---
export DB_SECRET_OCID="ocid1.vaultsecret.oc1.eu-frankfurt-1.amaaaaaaaenu5lyas5scqzuhlws7pzsg6jxsmcbybu2uecizvqs5p2ghbuda"
export COMPARTMENT_ID="ocid1.compartment.oc1..aaaaaaaa3mszlfmbw565py7b6wbcjt2jmeqqo3t5ikvyrmhjxynatcbtba7a"
export SUBNET_ID="ocid1.subnet.oc1.eu-frankfurt-1.aaaaaaaaxs2pscboxjlrvjtf5ez6izdopbhq5va4r4xukoxvkm7orduuifyq"
export OCI_REGION="eu-frankfurt-1"
export OCI_REGION_KEY="fra"
export OCI_TENANCY_NAMESPACE="frpowqeyehes"
export APP_NAME="rag-app"

echo "✅ Step 1/6: Environment configured."

# ==============================================================================
# PRE-FLIGHT CHECK: Verify Docker Login
# ==============================================================================
echo "➡️ Step 2/6: Verifying Docker login to OCIR..."
if ! docker pull ${OCI_REGION_KEY}.ocir.io/${OCI_TENANCY_NAMESPACE}/non-existent-image:latest 2>&1 | grep -q "unauthorized"; then
    echo "   ✅ Docker login confirmed."
else
    echo "   ❌ ERROR: Docker login failed or has expired."
    echo "   Please run the following command manually, replacing <YOUR_AUTH_TOKEN>:"
    echo "   echo \"<YOUR_AUTH_TOKEN>\" | docker login ${OCI_REGION_KEY}.ocir.io -u ${OCI_TENANCY_NAMESPACE}/${OCI_USERNAME} --password-stdin"
    exit 1
fi

# ==============================================================================
# SELF-HEALING: Clean and Recreate Fn Context
# ==============================================================================
echo "➡️ Step 3/6: Forcing a clean recreation of the Fn CLI context..."
rm -rf ~/.fn/contexts/oci-prod
fn create context oci-prod --provider oracle
fn use context oci-prod
echo "   ✅ Fn context 'oci-prod' has been reset and is now in use."

echo "➡️ Step 4/6: Configuring the Fn CLI context details..."
fn update context oracle.compartment-id "${COMPARTMENT_ID}"
fn update context api-url "https://functions.${OCI_REGION}.oci.oraclecloud.com"
fn update context registry "${OCI_REGION_KEY}.ocir.io/${OCI_TENANCY_NAMESPACE}/${APP_NAME}"
echo "   ✅ Fn context configured."

# ==============================================================================
# DEPLOYMENT: Build, Push, and Deploy
# ==============================================================================
echo "➡️ Step 5/6: Building and deploying the function... (This may take a few minutes)"
fn --verbose deploy --app ${APP_NAME}
echo "   ✅ Function deployed successfully."

# ==============================================================================
# CONFIGURATION: Apply Environment Variables
# ==============================================================================
echo "➡️ Step 6/6: Applying runtime configuration to the deployed function..."
fn config function ${APP_NAME} rag-ingestor DB_SECRET_OCID "${DB_SECRET_OCID}"
fn config function ${APP_NAME} rag-ingestor OCI_NAMESPACE "${OCI_TENANCY_NAMESPACE}"
echo "   ✅ Runtime configuration applied."

# ==============================================================================
# FINAL INSTRUCTIONS
# ==============================================================================
echo "--------------------------------------------------------------------------"
echo "🚀 DEPLOYMENT SUCCEEDED. The function is now deployed in OCI."
echo ""
echo "🔴 IMMEDIATE ACTION REQUIRED:"
echo "   1. Go to the OCI Console and create the Event Rule as described in the README."
echo "   2. Perform the End-to-End Test from the README to verify functionality."
echo ""
echo "🔒 POST-DEPLOYMENT SECURITY HARDENING (Phase 2):"
echo "   - We must now schedule the work to create a dedicated CI/CD user and"
echo "     replace the broad IAM policies with a secure, least-privilege version."
echo "--------------------------------------------------------------------------"