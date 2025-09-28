
#!/bin/bash
# ==============================================================================
# RAG Ingestor - Hardened Deployment Script v2.4 (Architecture Fix)
#
# THIS IS THE DEFINITIVE FIX.
# It addresses the ARM64-to-AMD64 cross-compilation issue by explicitly
# setting the Docker build platform. This prevents silent build failures of
# C extensions that lead to instant container crashes and 504 timeouts.
# ==============================================================================

# -e: Exit immediately if a command exits with a non-zero status.
# -u: Treat unset variables as an error when substituting.
# -o pipefail: The return value of a pipeline is its last non-zero status.
set -euo pipefail

# --- ACTION REQUIRED: Fill in these 2 values ---
export OCI_USERNAME="ven.ajie@protonmail.com" # e.g., 'firstname.lastname@example.com' or 'identity/user.name'
export TENANCY_OCID="ocid1.tenancy.oc1..aaaaaaaapk5a76iob5ujd7byfio3cmfosyj363ogf4hjmti6zm5ojksexgzq"

# --- Confirmed Values (No changes needed) ---
export DB_SECRET_OCID="ocid1.vaultsecret.oc1.eu-frankfurt-1.amaaaaaaaenu5lyas5scqzuhlws7pzsg6jxsmcbybu2uecizvqs5p2ghbuda"
export COMPARTMENT_ID="ocid1.compartment.oc1..aaaaaaaa3mszlfmbw565py7b6wbcjt2jmeqqo3t5ikvyrmhjxynatcbtba7a"
export SUBNET_ID="ocid1.subnet.oc1.eu-frankfurt-1.aaaaaaaaxs2pscboxjlrvjtf5ez6izdopbhq5va4r4xukoxvkm7orduuifyq"
export OCI_REGION="eu-frankfurt-1"
export OCI_REGION_KEY="fra"
export OCI_TENANCY_NAMESPACE="frpowqeyehes"
export APP_NAME="rag-app"

# --- Derived Values (DO NOT EDIT) ---
export FUNCTION_NAME=$(grep 'name:' func.yaml | awk '{print $2}')
export OCIR_REGISTRY="${OCI_REGION_KEY}.ocir.io/${OCI_TENANCY_NAMESPACE}/${APP_NAME}"

echo "✅ Step 1/6: Environment configured."
echo "   - Target Function: '${FUNCTION_NAME}' (from func.yaml)"

# ... (Steps 2, 3, and 4 are unchanged and correct) ...
echo "➡️ Step 2/6: Verifying Docker login to OCIR..."
if ! docker pull ${OCI_REGION_KEY}.ocir.io/${OCI_TENANCY_NAMESPACE}/non-existent-image:latest 2>&1 | grep -q "unauthorized"; then
    echo "   ✅ Docker login confirmed."
else
    echo "   ❌ ERROR: Docker login failed or has expired."
    echo "   Please run the following command manually, replacing <YOUR_AUTH_TOKEN>:"
    echo "   echo \"<YOUR_AUTH_TOKEN>\" | docker login ${OCI_REGION_KEY}.ocir.io -u ${OCI_TENANCY_NAMESPACE}/${OCI_USERNAME} --password-stdin"
    exit 1
fi

echo "➡️ Step 3/6: Forcing a clean recreation of the Fn CLI context..."
echo "   -> Switching to 'default' context as a safe harbor."
fn use context default
echo "   -> Deleting old 'oci-prod' context (if it exists)."
fn delete context oci-prod || true
echo "   -> Creating a fresh 'oci-prod' context."
fn create context oci-prod --provider oracle
fn use context oci-prod
echo "   ✅ Fn context 'oci-prod' has been reset and is now in use."

echo "➡️ Step 4/6: Configuring the Fn CLI context details..."
fn update context oracle.compartment-id "${COMPARTMENT_ID}"
fn update context api-url "https://functions.${OCI_REGION}.oci.oraclecloud.com"
fn update context registry "${OCIR_REGISTRY}"
echo "   ✅ Fn context configured."

# ==============================================================================
# DEPLOYMENT: Build, Push, and Deploy
# ==============================================================================
echo "➡️ Step 5/6: Building and deploying the function for AMD64 architecture..."
echo "   - Host Architecture: $(uname -m)"
echo "   - Target Architecture: amd64"

# CRITICAL FIX: Set the build platform for Docker. This forces the build to
# create an image compatible with the OCI Functions runtime (amd64).
export DOCKER_DEFAULT_PLATFORM=linux/amd64

# '--no-bump' is a best practice to prevent the CLI from modifying your func.yaml.
fn --verbose deploy --app ${APP_NAME} --no-bump

echo "   ✅ Function deployed successfully."

# ==============================================================================
# CONFIGURATION: Apply Environment Variables
# ==============================================================================
echo "➡️ Step 6/6: Applying runtime configuration to the deployed function..."
fn config function ${APP_NAME} ${FUNCTION_NAME} DB_SECRET_OCID "${DB_SECRET_OCID}"
fn config function ${APP_NAME} ${FUNCTION_NAME} OCI_NAMESPACE "${OCI_TENANCY_NAMESPACE}"
echo "   ✅ Runtime configuration applied."

# ==============================================================================
# FINAL INSTRUCTIONS
# ==============================================================================
echo "--------------------------------------------------------------------------"
echo "🚀 DEPLOYMENT SUCCEEDED. The function '${FUNCTION_NAME}' is now live in OCI."
echo "   The architecture mismatch has been resolved."
echo ""
echo "🔴 IMMEDIATE ACTION REQUIRED:"
echo "   1. Trigger the function and check the logs in the OCI Console."
echo "   2. You should now see your custom JSON logs from the application."
echo "--------------------------------------------------------------------------"