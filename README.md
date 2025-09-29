# OCI Serverless Function: RAG Ingestor - v5.0 

This repository contains the source code and the definitive, battle-tested deployment guide for the `rag-ingestor` OCI Function. This document has been hardened with lessons learned from a series of production-blocking incidents to ensure a robust, repeatable, and understandable deployment process.

## 1. Architectural Overview

This function is a critical component of the RAG ecosystem. It is a serverless, event-driven service responsible for securely and efficiently loading indexed codebase data into a central PostgreSQL database.

**Data Flow:**
```
+------------------+      +----------------------+      +--------------------+      +------------------+
| GitHub Actions   |----->| OCI Object Storage   |----->| OCI Function       |----->| PostgreSQL DB    |
| (CI/CD Producer) |      | (rag-codebase-inbox) |      | (This Function)    |      | (Central DB)     |
+------------------+      +----------------------+      +--------------------+      +------------------+
```

**Core Technology:** This function is implemented as a self-contained **FastAPI application** running in a custom Docker container.

**Architectural Decision (PB-20250924-01): The "FDK-less" Architecture**
The decision to bypass the official Oracle Python FDK (`fdk`) was made after a critical, unfixable build deadlock was discovered. The `fdk`'s dependency on `httptools==0.4.0` is fundamentally incompatible with the C-API of Python 3.11+. This "FDK-less" approach resolves the build deadlock, unlocks the use of modern Python versions, and improves local testability.

## 2. Prerequisites

Ensure you have the following configured in your GitHub repository's **Settings -> Secrets and variables -> Actions**:

**Repository Secrets:**
*   `OCI_PRIVATE_KEY`: The private key for your OCI API user.
*   `OCI_USER_OCID`: The OCID of the API user.
*   `OCI_FINGERPRINT`: The fingerprint of the API key.
*   `OCI_TENANCY_OCID`: The OCID of your tenancy.
*   `OCI_USERNAME`: The full username for OCIR login (e.g., `service-user-ci-cd`).
*   `OCIR_AUTH_TOKEN`: The OCI Auth Token for the user (for Docker login).
*   `OCI_FN_APP_OCID`: The OCID of the **Function Application** (`rag-app`) that will contain this function.

**Repository Variables:**
*   `OCI_REGION`: The OCI region identifier (e.g., `eu-frankfurt-1`).
*   `OCI_REGION_KEY`: The region key for OCIR (e.g., `fra`).

## 3. Critical Infrastructure Setup: The "Gotcha" Checklist

Before the first deployment, the following OCI resources and policies **MUST** be correctly configured. Failure to do so will result in the cryptic and misleading errors detailed in the troubleshooting section.

### 3.1. VCN Networking

The function runs in a private subnet. This subnet's configuration is critical for allowing it to be invoked and to call other OCI services.
*   **Route Table:** The subnet's Route Table **MUST** have a rule directing traffic for `All <region> Services in Oracle Services Network` to a **Service Gateway**.
*   **Security List:** The subnet's Security List **MUST** have a **Stateful Ingress** rule allowing traffic from `Source Service: All <region> Services in Oracle Services Network` on at least **TCP port 443**.

### 3.2. IAM Policies

Two sets of permissions are required: one for the CI/CD pipeline to deploy resources, and one for the function to run.

**A. For the CI/CD User (the user associated with `OCI_USER_OCID`):**
This user's group needs permission to push container images. This policy should be in the **root compartment**.
```
# Allows the CI/CD user to create repositories and push images into the project compartment.
Allow group <Your-CI-CD-User-Group> to manage repos in compartment RAG-Project
```

**B. For the Function's Runtime (Resource Principals):**
This policy allows the running function to authenticate and access other services. This policy should be in the **root compartment**.
```
# Allows the function to identify itself and its group membership.
Allow dynamic-group RAGIngestorFunctionDynamicGroup to inspect dynamic-groups in tenancy

# Allows the function to be placed in a VCN subnet.
Allow dynamic-group RAGIngestorFunctionDynamicGroup to use virtual-network-family in tenancy

# Allows the function to read the database secret from the vault.
Allow dynamic-group RAGIngestorFunctionDynamicGroup to read secret-family in compartment RAG-Project

# Allows the function to read objects from the specific inbox bucket.
Allow dynamic-group RAGIngestorFunctionDynamicGroup to read objects in compartment RAG-Project where target.bucket.name = 'rag-codebase-inbox'
```

### 3.3. The Dynamic Group

The Dynamic Group is the link between your function resource and its IAM permissions.
*   **CRITICAL LESSON:** Identifying the function by its OCID (`resource.id = 'ocid...'`) is **brittle**. The OCID changes every time the function is recreated, requiring a manual update to this rule.
*   **ROBUST SOLUTION:** Identify the function by a tag that is applied automatically by the CI/CD pipeline. This makes the infrastructure resilient to recreation.

**Recommended Dynamic Group Rule:**
```
# Match any function that has been tagged by our CI/CD pipeline.
ALL {resource.type = 'fnfunc', resource.defined_tags.MyTags.name = 'rag-ingestor-fn'}
```
*(Note: This requires a one-time setup of the `MyTags` Tag Namespace in OCI.)*

## 4. Canonical Deployment Method: Manual Creation, Automated Updates

**CRITICAL LESSON LEARNED:** The automated tools (`fn` CLI, `oci` CLI) have proven unreliable for **creating** a new function with its identity correctly provisioned. The only foolproof method is to create the function manually via the OCI Console UI the first time. All subsequent deployments can then be automated.

### 4.1. The One-Time Manual Setup

1.  **Run the CI/CD Pipeline Once:** Push a commit to `main`. The pipeline will fail at the final "Create or Update" step because the function doesn't exist yet, but the most important step—**building and pushing the image to OCIR**—will succeed.
2.  **Verify the Image:** Go to the OCI Console -> **Container Registry**. Select the `RAG-Project` compartment. You will now see the `rag-project/rag-app/rag-ingestor` repository. This confirms the image exists.
3.  **Create the Function Manually:**
    *   Navigate to your `rag-app` Function Application.
    *   Click **"Create function"** and select **"Create from existing image"**.
    *   **Name:** `rag-ingestor`
    *   **Image Repository:** Select `rag-project/rag-app/rag-ingestor` from the (now populated) dropdown.
    *   **Image Tag:** Select the latest commit SHA from the dropdown.
    *   Set Memory to `1024` and Timeout to `120`.
    *   Click **"Create"**.
4.  **Update the Dynamic Group:** Copy the OCID of the function you just created and update your Dynamic Group rule to match it. This is the final manual wiring step.

### 4.2. Ongoing Automated Updates

After the one-time manual setup, all subsequent pushes to `main` will be handled by the CI/CD pipeline defined in `.github/workflows/deploy.yml`. The pipeline will build and push a new image, then use the `oci fn function update` command to point the existing, healthy function to the new image version.

## 5. Operations and Troubleshooting: The Battle-Tested Diagnostic Flow

This flow is the result of our debugging journey and addresses the specific, misleading errors encountered.

### Symptom: No Logs at All After Triggering

*   **Diagnosis:** The invocation event is not reaching the function.
*   **Action Checklist:**
    1.  **Event Rule Wiring:** Go to **Events Service -> Rules**. Edit your rule. Is the **Action** correctly pointing to the current, existing `rag-ingestor` function? This link breaks every time the function is recreated and must be manually re-wired.
    2.  **VCN Networking:** Review the checklist in **Section 3.1**. A missing Route Table rule or Security List rule is the most common cause.

### Symptom: `502` Crash on Startup with `FATAL: ... private.pem` Error

*   **Diagnosis:** This is the most critical and misleading error. It means the function is running but **lacks a Resource Principal identity**. The platform has not injected the necessary credentials.
*   **Action Checklist:**
    1.  **The Primary Cause:** Was the function created by an automated script (`fn deploy` or `oci fn function create`)? These tools have proven unreliable. **The Fix:** Delete the function and follow the **manual creation process** in **Section 4.1**.
    2.  **The Secondary Cause:** Is the **Dynamic Group** rule correct? It **MUST** match the OCID of the currently deployed function. If you have recreated the function, you **MUST** update this rule manually.

### Symptom: `502` Crash on Startup with `NoSuchModuleError: ... psycopg`

*   **Diagnosis:** The application code is trying to load a binary package that was compiled for the wrong CPU architecture.
*   **Cause:** The Docker image was built without specifying the target platform.
*   **The Fix:** Ensure the `docker/build-push-action` step in your `deploy.yml` contains the line: `platforms: linux/amd64`.

### Symptom: `504`

The `504` error is a generic, misleading symptom. It rarely means a true timeout. It almost always means the **container crashed instantly**, and the platform timed out while waiting for a response that would never come.

### Symptom: CI/CD Fails with `repository name must be lowercase`

*   **Diagnosis:** The Docker client has a strict rule that image repository paths must be lowercase.
*   **The Fix:** Ensure the image tag constructed in your `deploy.yml` uses a lowercase version of your compartment name (e.g., `.../rag-project/...`). The OCI backend is smart enough to map this to your mixed-case `RAG-Project` compartment.

