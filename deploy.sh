
#!/bin/bash
set -e # Exit immediately if a command exits with a non-zero status.

# ==============================================================================
# PHASE 0: CONFIGURATION - UPDATE THE REQUIRED VALUES
# ==============================================================================
# --- ACTION REQUIRED: Fill in these 3 values ---
export OCI_USER_AUTH_TOKEN="<PASTE_YOUR_PERSONAL_AUTH_TOKEN_HERE>"
export OCI_USERNAME="<your-oci-username-for-docker-login>" # e.g., 'firstname.lastname@example.com' or 'identity/user.name'
export TENANCY_OCID="<PASTE_YOUR_TENANCY_OCID_HERE>"

# --- Confirmed Values (No changes needed) ---
export DB_SECRET_OCID="ocid1.vaultsecret.oc1.eu-frankfurt-1.amaaaaaaaenu5lyas5scqzuhlws7pzsg6jxsmcbybu2uecizvqs5p2ghbuda"
export COMPARTMENT_ID="ocid1.compartment.oc1..aaaaaaaa3mszlfmbw565py7b6wbcjt2jmeqqo3t5ikvyrmhjxynatcbtba7a"
export SUBNET_ID="ocid1.subnet.oc1.eu-frankfurt-1.aaaaaaaaxs2pscboxjlrvjtf5ez6izdopbhq5va4r4xukoxvkm7orduuifyq"
export OCI_REGION="eu-frankfurt-1"
export OCI_REGION_KEY="fra"
export OCI_TENANCY_NAMESPACE="frpowqeyehes"
export APP_NAME="rag-app"

echo "✅ Environment configured for initial deployment."

# ==============================================================================
# PHASE 1: PROVISION CORE INFRASTRUCTURE (Idempotent)
# ==============================================================================
# These commands are safe to re-run. They will not fail if the resources already exist.
echo "➡️ Ensuring Object Storage bucket 'rag-codebase-inbox' exists..."
oci os bucket create --compartment-id $COMPARTMENT_ID --name "rag-codebase-inbox" --object-events-enabled true 2>/dev/null || echo "Bucket already exists."

echo "➡️ Ensuring Functions Application '$APP_NAME' exists..."
oci fn application create --compartment-id $COMPARTMENT_ID --display-name $APP_NAME --subnet-ids "[\"$SUBNET_ID\"]" 2>/dev/null || echo "Application already exists."

# ==============================================================================
# PHASE 2: IAM POLICIES (SKIPPED)
# ==============================================================================
echo "⚠️ IAM policy creation is SKIPPED for this initial deployment."
echo "Relying on existing broader policies. Hardening will be done post-deployment."

# ==============================================================================
# PHASE 3: DEPLOY AND CONFIGURE THE FUNCTION
# ==============================================================================
echo "➡️ Logging in to OCI Container Registry using your personal Auth Token..."
#echo "${OCI_USER_AUTH_TOKEN}" | docker login ${OCI_REGION_KEY}.ocir.io -u #${OCI_TENANCY_NAMESPACE}/${OCI_USERNAME} --password-stdin
echo "wD{WEAP3987n>5}3#<V]" | docker login fra.ocir.io -u frpowqeyehes/ven.ajie@protonmail.com --password-stdin

echo "➡️ Configuring Fn CLI context..."
# This command will create the context or allow us to proceed if it already exists.
fn create context oci-prod --provider oracle 2>/dev/null || echo "Context 'oci-prod' already exists."
fn use context oci-prod
fn update context oracle.compartment-id "${COMPARTMENT_ID}"
fn update context api-url "https://functions.${OCI_REGION}.oci.oraclecloud.com"
# The registry path must include the application name to push the image to the correct repository.
fn update context registry "${OCI_REGION_KEY}.ocir.io/${OCI_TENANCY_NAMESPACE}/${APP_NAME}"

echo "➡️ Building and deploying function 'rag-ingestor' to application '${APP_NAME}'..."
# The 'fn deploy' command handles both building the Docker image and pushing it.
fn deploy --app ${APP_NAME}

echo "➡️ Applying runtime configuration to the deployed function..."
fn config function ${APP_NAME} rag-ingestor DB_SECRET_OCID "${DB_SECRET_OCID}"
fn config function ${APP_NAME} rag-ingestor OCI_NAMESPACE "${OCI_TENANCY_NAMESPACE}"

echo "✅ Deployment and configuration complete."

# ==============================================================================
# PHASE 4: NEXT STEPS
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
echo "     replace the broad IAM policies with the secure, consolidated version."
echo "--------------------------------------------------------------------------"