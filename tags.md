
#### Part 2: The Permanent, Production-Grade Fix (Automated Tagging)

The manual fix is brittle. The next time you recreate the function, you will have to do it again. The correct architectural solution is to stop identifying the function by its fragile OCID and start identifying it by a stable **tag**.

1.  **Modify the Dynamic Group to Match a Tag:**
    *   Go back to your `RAGIngestorFunctionDynamicGroup` and click **"Edit"**.
    *   Replace the OCID-based rule with a tag-based rule. This rule says, "Match any function that has a specific tag."

    ```
    ALL {resource.type = 'fnfunc', resource.defined_tags.MyTags.name = 'rag-ingestor-fn'}
    ```
    *(Note: You will need to create a Tag Namespace called `MyTags` and a Tag Key called `name` in the OCI console under Governance & Administration -> Tag Namespaces. This is a one-time setup.)*

2.  **Modify Your `deploy.yml` to Apply the Tag:**
    *   In your CI/CD workflow, we will add the `--defined-tags` parameter to the `oci fn function create/update` commands. This automatically "stamps" the function with the correct identifier during deployment.

    **In your `.github/workflows/deploy.yml`, find the step `8. Create or Update OCI Function` and modify it:**

    ```yaml
    - name: 8. Create or Update OCI Function
      run: |
        # Define the tag as a JSON string
        TAG_JSON='{"MyTags": {"name": "rag-ingestor-fn"}}'

        FUNCTION_OCID=$(oci fn function list --application-id ${{ secrets.OCI_FN_APP_OCID }} --display-name rag-ingestor --query "data[0].id" --raw-output || true)
        
        if [ -z "$FUNCTION_OCID" ]; then
          echo "Function 'rag-ingestor' not found. Creating it with tag..."
          oci fn function create \
            --application-id ${{ secrets.OCI_FN_APP_OCID }} \
            --display-name rag-ingestor \
            --image "${{ env.FULL_IMAGE_NAME }}" \
            --memory-in-mbs 1024 \
            --timeout-in-seconds 120 \
            --defined-tags "$TAG_JSON"
        else
          echo "Function 'rag-ingestor' found. Updating it with tag..."
          oci fn function update \
            --function-id "$FUNCTION_OCID" \
            --image "${{ env.FULL_IMAGE_NAME }}" \
            --defined-tags "$TAG_JSON"
        fi
    ```
