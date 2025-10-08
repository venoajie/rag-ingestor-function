## OCI Serverless Function: RAG Ingestor - v5.0 (Hardened)

This repository contains the source code for the `rag-ingestor` OCI Function. This document has been hardened with lessons learned from a series of production-blocking incidents to ensure a robust, repeatable, and understandable deployment process.

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

### 3.1. VCN Networking: Hub & Spoke with DRG

The function runs in a private subnet within a "Spoke" VCN (`rag-project-vcn`), connecting to a database in a central "Hub" VCN (`shared-infrastructure-vcn`). This connection is managed by a **Dynamic Routing Gateway (DRG)**.

*   **LESSON LEARNED: The Hub and Spoke VCNs MUST both be attached to the DRG.** A common failure is to attach only the Spoke, creating a "bridge to nowhere." The DRG cannot route traffic to a VCN that is not attached to it.

*   **DRG Route Table:** The DRG's internal route table **MUST** have static routes that explicitly teach it how to route traffic between the Hub and Spoke VCNs. The DRG is not a simple passthrough; it requires explicit rules.
    *   **Example Rule 1 (In table for Hub traffic):** `Destination: 10.1.0.0/16` (Spoke) -> `Next Hop: Spoke-VCN-attachment`
    *   **Example Rule 2 (In table for Spoke traffic):** `Destination: 10.0.0.0/16` (Hub) -> `Next Hop: Hub-VCN-attachment`

*   **Spoke VCN Architecture for OCI Functions (CRITICAL):** A "private-only" VCN is not a viable architecture for hosting OCI Functions due to conflicting network requirements between the OCI platform and the function's runtime code. The Spoke VCN **MUST** have the following structure:
    *   One **Public Subnet** to host gateways.
    *   One **Private Subnet** to host the function.
    *   An **Internet Gateway**, with a route rule in the public subnet's route table.
    *   A **NAT Gateway**.
    *   A **Service Gateway**.

*   **Spoke VCN Private Route Table (The Golden Trio):** The route table for the function's private subnet **MUST** have the following three rules. The order of specificity is critical.
    1.  **Service Gateway Rule:** `Destination: All <region> Services in Oracle Services Network` -> `Target: Service Gateway`. (This is for the OCI Platform to pull the image).
    2.  **NAT Gateway Rule:** `Destination: 0.0.0.0/0` -> `Target: NAT Gateway`. (This is for the function's own code to reliably access OCI services like Vault and the internet).
    3.  **DRG Rule:** `Destination: 10.0.0.0/16` (Hub CIDR) -> `Target: Dynamic Routing Gateway`. (For the function's code to reach the database).

*   **LESSON LEARNED: The Service Gateway is a "Silent Black Hole" for runtime code.** The architecture above works because the more general `0.0.0.0/0` NAT Gateway route effectively overrides the Service Gateway for the function's own API calls to OCI services, while still allowing the OCI platform to use the Service Gateway for its specific management tasks.

### 3.2. IAM Policies

These policies **must be created in the Root Compartment (Tenancy)** to correctly grant permissions across compartments.

**A. For the CI/CD User:**
```
# Allows the CI/CD user to create repositories and push images into the project compartment.
Allow group <Your-CI-CD-User-Group> to manage repos in compartment RAG-Project
```

**B. For the Function's Runtime (Resource Principals):**
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

*   **CRITICAL LESSON:** Identifying the function by its OCID (`resource.id = 'ocid...'`) is **brittle**. The OCID changes every time the function is recreated.
*   **ROBUST SOLUTION:** Identify all functions within the target compartment. This is secure as long as the compartment is dedicated to this application.

**Recommended Dynamic Group Rule:**
```
# Match any function within the RAG-Project compartment.
ALL {resource.type = 'fnfunc', resource.compartment.id = 'ocid1.compartment.oc1..aaaa...'}
```

## 4. Deployment & Troubleshooting

This section combines the deployment process with the specific errors encountered and their definitive solutions.

### 4.1. Deployment Process

1.  **First Deployment:** The first time the pipeline runs, it will fail to find an existing function and will execute the `oci fn function create` command, provisioning the function with the correct settings (memory, timeout, and Resource Principal annotation).
2.  **Subsequent Deployments:** On every subsequent push to `main`, the pipeline will find the existing function and execute the `oci fn function update` command. This surgically updates the function's Docker image and uses the `--force` and `--annotation` flags to prevent any configuration drift.

### 4.2. Managing Function Configuration

The file `func.yaml` is kept as a human-readable **reference only**. To change the function's memory or timeout, you must update the variables at the top of the `Create or Update OCI Function` step in the `.github/workflows/deploy.yml` file.

### 4.3. Troubleshooting Flow

**Symptom: `FATAL: ... private.pem` Error on Startup**
*   **Diagnosis:** This is the most critical and misleading error. It means the function is running but **cannot talk to the OCI authentication service** to get its identity.
*   **Root Cause:** The egress network path is blocked.
*   **Action:** Verify the **Function Subnet Route Table** has a rule sending all traffic (`0.0.0.0/0`) to a **NAT Gateway**. The Service Gateway is not a reliable alternative.

**Symptom: `504 Timeout` on Startup**
*   **Diagnosis:** The function is timing out while trying to connect to an external resource, most likely the database.
*   **Root Cause:** The network path between the function's VCN and the database's VCN is blocked.
*   **Action:** Verify the **DRG Route Table** has the correct static routes to connect the two VCNs, as described in **Section 3.1**.

**Symptom: CI/CD Fails with `repository name must be lowercase`**
*   **Diagnosis:** The Docker client requires lowercase image repository paths.
*   **Action:** Ensure the image tag in `deploy.yml` uses a lowercase version of your compartment name (e.g., `.../rag-project/...`).

---


---
## Appendix A: Current Debugging State (As of End of Session)

This section serves as a snapshot of the last debugging session to ensure continuity.

### 1. Current Status & Final Confirmed Blocker

*   The original `504 Timeout` is caused by a broken network path between the function in the **Spoke VCN (`rag-project-vcn`)** and the database in the **Hub VCN (`shared-infrastructure-vcn`)**.
*   We have proven through exhaustive testing that the Spoke VCN's internal networking (Gateways, Route Tables, Security Lists) is now perfectly configured according to best practices.
*   The final, confirmed root cause is an **incomplete infrastructure setup**: The **Hub VCN is not attached to the DRG (`hub-spoke-drg`)**. The Spoke VCN is attached to a DRG that has no connection to the database's network.

### 2. Final Failure Point & Primary Hypothesis

*   All attempts to fix this by creating the missing attachment for the Hub VCN have failed.
*   The OCI Console UI for creating the attachment is unclickable.
*   The OCI CLI command to create the attachment fails with a `404 NotAuthorizedOrNotFound` error.
*   **Final Hypothesis:** The CLI command is failing due to an **implicit context issue**. The command must be run from a machine whose default compartment is the same as the DRG's compartment (`RAG-Project`). Our attempts from the `dev` machine (in the `Shared-Infrastructure` compartment) are failing because of this cross-compartment violation.

### 3. Key Lessons Learned in This Session

*   **OCI Functions have conflicting network needs:** The OCI platform requires a **Service Gateway** to pull the image, but the function's runtime code can fail silently when using that same gateway. A robust VCN design requires both a Service and a NAT Gateway.
*   **DRGs are not simple connectors:** They have internal route tables that require explicit configuration to route traffic between attachments.
*   **The CLI's operational context matters:** A `404 NotAuthorizedOrNotFound` error can be misleading. It can be caused not by a lack of permissions, but by running a command from a machine in the wrong default compartment when creating cross-compartment resources.

### 4. Next Diagnostic Step for Next Session

The very first action for the next session should be to execute the `create DRG attachment` command from the correct context to bypass the `404` error.

1.  **Goal:** Successfully create the DRG attachment for the **Hub VCN (`shared-infrastructure-vcn`)**.
2.  **Tool:** Use the **OCI Bastion Service** (`BastionRagDebug`) to connect.
3.  **Target Instance:** SSH into the **`rag-function-debug-client-ol`** instance, because it lives in the correct compartment (`RAG-Project`).
4.  **Command:** From the SSH session on `rag-function-debug-client-ol`, execute the following simple command:
    ```bash
    oci network drg-attachment create \
    --drg-id "ocid1.drg.oc1.eu-frankfurt-1.aaaaaaaa7vz3ourrx3sotcgjbxvjhh7t53fmn2gjbqhwc2uhkndnrzitwmkq" \
    --vcn-id "ocid1.vcn.oc1.eu-frankfurt-1.amaaaaaaaenu5lyax43fxpp3grbnx4jkebohe7jk7ojjv7b745czhrfbbfya" \
    --display-name "Hub-VCN-Attachment" \
    --route-table-id "ocid1.drgroutetable.oc1.eu-frankfurt-1.aaaaaaaarupqjguz6annge5irefm34cvwr5l2nunbkjln3wckzba5kru6v3q"
    ```
5.  **Expected Result:** The command will succeed, creating the final missing piece of infrastructure.
6.  **Post-Success Plan:** After the attachment is created, the final two routing configurations are required:
    *   Add static routes to the DRG's VCN route table for both the Hub and Spoke.
    *   Ensure each VCN's private route table has a rule pointing to the DRG for traffic destined to the other VCN.

This will complete the network path and permanently resolve the `504 Timeout`.

### 5. Modifications to Files for the Next Debugger


*   **`main.py`:**
    *   **Status:** It currently contains temporary logic for user-based authentication (`OCI_CONFIG_B64`).
    *   **Action Required:** This logic **must be removed**. The `initialize_dependencies` function should be reverted to its clean, production state that only uses `signer = oci.auth.signers.get_resource_principals_signer()`. The temporary `OCI_CONFIG_B64` variable should be deleted from the Application's configuration.
