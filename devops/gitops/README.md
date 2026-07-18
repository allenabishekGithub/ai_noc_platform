# Argo CD GitOps

This directory makes Argo CD the reconciler for the `tfs-agentic` Helm chart.
The `Application` reads the chart from the `main` branch of the GitHub
repository, renders it with `values.gitops.yaml`, and automatically syncs,
self-heals drift, and prunes resources removed from Git.

## Ownership model

Argo CD renders Helm charts; it does not run `helm upgrade` or maintain the
Helm release history. Once the Application is healthy, make Git commits and
push them to `main`; do not use `scripts/deploy-helm.sh` to modify the same
release.

The current Helm deployment and the Argo CD Application use the same release
name, resource names, namespace, and image tags. Argo CD can therefore adopt
the running resources without a delete-and-recreate migration. Do **not** run
`helm uninstall tfs-agentic` after the Application is healthy: it would delete
the resources that Argo CD is managing.

## Bootstrap sequence

1. Commit and push the Helm and GitOps files to `origin/main`. Argo CD cannot
   reconcile files that only exist in this VM checkout.

2. Create the runtime Secret outside Git:

   ```bash
   ./scripts/create-gitops-runtime-secret.sh
   ```

   For production, replace this helper with External Secrets, SOPS, or another
   approved secret-management system. Never commit provider keys or controller
   credentials.

3. Install the pinned Argo CD controller and register the application:

   ```bash
   ./scripts/bootstrap-argocd.sh
   ```

   The script defaults to Argo CD `v3.4.2`. Override `ARGOCD_VERSION` only with
   a reviewed supported release.

4. Verify reconciliation:

   ```bash
   microk8s kubectl -n argocd get applications.argoproj.io
   microk8s kubectl -n tfs-agentic get pods
   ```

5. Access the Argo CD UI locally:

   ```bash
   microk8s kubectl -n argocd port-forward svc/argocd-server 8080:443
   ```

   Retrieve the initial password with the command printed by the bootstrap
   script. Change the password and remove the initial-admin Secret afterwards.

## Day-two deployment flow

```bash
git add devops/helm devops/gitops scripts
git commit -m "Configure TFS Agentic GitOps"
git push origin main
```

Argo CD notices the pushed revision and applies it. Build and push a new image
first when application code changes, then commit the new immutable image tag
in `values.gitops.yaml`. Avoid using the mutable `dev` tag in production.
