
#!/bin/bash
# ==============================================================================
# RAG Ingestor - Hardened Deployment Script  v3.1 (Cache Invalidation)
# FINAL VERSION. This adds '--no-cache' to the Docker build command.
# This is the ultimate guarantee that no stale or incorrect-architecture
# layers from previous local builds are being reused. The build will be
# slower but 100% clean.
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
# We now read both name and version to construct the image tag.
export FUNCTION_NAME=$(grep 'name:' func.yaml | awk '{print $2}')
export FUNCTION_VERSION=$(grep 'version:' func.yaml | awk '{print $2}')
export OCIR_REGISTRY="${OCI_REGION_KEY}.ocir.io/${OCI_TENANCY_NAMESPACE}/${APP_NAME}"
export FULL_IMAGE_NAME="${OCIR_REGISTRY}/${FUNCTION_NAME}:${FUNCTION_VERSION}"

echo "✅ Step 1/7: Environment configured."
echo "   - Target Function: '${FUNCTION_NAME}'"
echo "   - Target Image: '${FULL_IMAGE_NAME}'"

# ... (Steps 2, 3, and 4 are unchanged and correct) ...
echo "➡️ Step 2/7: Verifying Docker login to OCIR..."
if ! docker pull ${OCI_REGION_KEY}.ocir.io/${OCI_TENANCY_NAMESPACE}/non-existent-image:latest 2>&1 | grep -q "unauthorized"; then
    echo "   ✅ Docker login confirmed."
else
    echo "   ❌ ERROR: Docker login failed or has expired. Please re-authenticate."
    exit 1
fi

echo "➡️ Step 3/7: Forcing a clean recreation of the Fn CLI context..."
fn use context default >/dev/null
fn delete context oci-prod || true
fn create context oci-prod --provider oracle >/dev/null
fn use context oci-prod >/dev/null
echo "   ✅ Fn context 'oci-prod' has been reset."

echo "➡️ Step 4/7: Configuring the Fn CLI context details..."
fn update context oracle.compartment-id "${COMPARTMENT_ID}" >/dev/null
fn update context api-url "https://functions.${OCI_REGION}.oci.oraclecloud.com" >/dev/null
fn update context registry "${OCIR_REGISTRY}" >/dev/null
echo "   ✅ Fn context configured."

# ==============================================================================
# NEW STEP 5: Manually Build and Push the Correct Architecture
# ==============================================================================
echo "➡️ Step 5/7: Building image for linux/amd64 from scratch (--no-cache)..."
# This is the most critical command. It forces the build for the correct platform.
# '--load' makes the image available to the local docker daemon for the push command.
docker buildx build --no-cache --platform linux/amd64 -t "${FULL_IMAGE_NAME}" . --load

echo "   ✅ Build complete."
echo "➡️ Step 6/7: Pushing the correctly built image to OCIR..."
docker push "${FULL_IMAGE_NAME}"
echo "   ✅ Image pushed successfully."

# ==============================================================================
# UPDATED STEP 7: Deploy Pre-Built Image
# ==============================================================================
echo "➡️ Step 7/7: Deploying the function using the pre-built image..."
# We now use the '--image' flag to tell 'fn' to skip building and use what we pushed.
fn --verbose deploy --app ${APP_NAME} --image "${FULL_IMAGE_NAME}"

echo "   ✅ Function deployed successfully."
# Configuration is not needed again as it persists on the function object unless changed.

echo "--------------------------------------------------------------------------"
echo "🚀 DEPLOYMENT SUCCEEDED. The correct image architecture has been enforced."
echo "   Trigger the function and check the logs in the OCI Console."
echo "--------------------------------------------------------------------------"