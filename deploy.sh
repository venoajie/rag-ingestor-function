
#!/bin/bash
# ==============================================================================
# RAG Ingestor - Hardened Deployment Script v2.3
#
# This version adds:
#   - Dynamic function name parsing from func.yaml to prevent config errors.
#   - Stricter shell settings (`-uo pipefail`) for improved safety.
#   - Centralized OCIR registry variable for maintainability (DRY principle).
# ==============================================================================

# --- Stricter Shell Settings ---
# -e: Exit immediately if a command exits with a non-zero status.
# -u: Treat unset variables as an error when substituting.
# -o pipefail: The return value of a pipeline is the status of the last
#              command to exit with a non-zero status.
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
# IMPROVEMENT 1: Derive function name from the canonical source: func.yaml
export FUNCTION_NAME=$(grep 'name:' func.yaml | awk '{print $2}')
# IMPROVEMENT 2: Centralize the registry URL to avoid repetition.
export OCIR_REGISTRY="${OCI_REGION_KEY}.ocir.io/${OCI_TENANCY_NAMESPACE}/${APP_NAME}"

echo "✅ Step 1/6: Environment configured."
echo "   - Target Function: '${FUNCTION_NAME}' (from func.yaml)"

# ==============================================================================
# PRE-FLIGHT CHECK: Verify Docker Login
# ==============================================================================
echo "➡️ Step 2/6: Verifying Docker login to OCIR..."
# The `docker pull` command is a functional check of credentials.
if ! docker pull ${OCI_REGION_KEY}.ocir.io/${OCI_TENANCY_NAMESPACE}/non-existent-image:latest 2>&1 | grep -q "unauthorized"; then
    echo "   ✅ Docker login confirmed."
else
    echo "   ❌ ERROR: Docker login failed or has expired."
    echo "   Please run the following command manually, replacing <YOUR_AUTH_TOKEN>:"
    echo "   echo \"<YOUR_AUTH_TOKEN>\" | docker login ${OCI_REGION_KEY}.ocir.io -u ${OCI_TENANCY_NAMESPACE}/${OCI_USERNAME} --password-stdin"
    exit 1
fi

# ==============================================================================
# SELF-HEALING: Clean and Recreate Fn Context (Corrected Logic)
# ==============================================================================
echo "➡️ Step 3/6: Forcing a clean recreation of the Fn CLI context..."
echo "   -> Switching to 'default' context as a safe harbor."
fn use context default

echo "   -> Deleting old 'oci-prod' context (if it exists)."
fn delete context oci-prod || true # '|| true' prevents failure if it doesn't exist.

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
echo "➡️ Step 5/6: Building and deploying the function... (This may take a few minutes)"
# The Fn CLI automatically bumps the version in func.yaml unless you add '--no-bump'.
# This is usually desired for development deploys.
fn --verbose deploy --app ${APP_NAME}
echo "   ✅ Function deployed successfully."

# ==============================================================================
# CONFIGURATION: Apply Environment Variables
# ==============================================================================
echo "➡️ Step 6/6: Applying runtime configuration to the deployed function..."
# IMPROVEMENT 3: Use the dynamically parsed function name.
fn config function ${APP_NAME} ${FUNCTION_NAME} DB_SECRET_OCID "${DB_SECRET_OCID}"
fn config function ${APP_NAME} ${FUNCTION_NAME} OCI_NAMESPACE "${OCI_TENANCY_NAMESPACE}"
echo "   ✅ Runtime configuration applied."

# ==============================================================================
# FINAL INSTRUCTIONS
# ==============================================================================
echo "--------------------------------------------------------------------------"
echo "🚀 DEPLOYMENT SUCCEEDED. The function '${FUNCTION_NAME}' is now live in OCI."
echo ""
echo "🔴 IMMEDIATE ACTION REQUIRED:"
echo "   1. Go to the OCI Console to create/verify the Event Rule targeting this function."
echo "   2. Perform the End-to-End Test from the README to verify functionality."
echo ""
echo "🔒 POST-DEPLOYMENT SECURITY HARDENING (Phase 2):"
echo "   - This script uses broad user credentials. The next step is to create a"
echo "     dedicated CI/CD user with a least-privilege IAM policy."
echo "--------------------------------------------------------------------------"