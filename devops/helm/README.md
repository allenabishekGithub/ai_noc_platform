# Helm deployment

`tfs-agentic` is the Helm chart for the Agentic Module and its TFS MCP
server. It replaces the imperative ConfigMap/Secret creation, per-manifest
`kubectl apply`, image patching, and rollout restart sequence previously used
by the Kubernetes wrapper scripts.

## Local MicroK8s deployment

MicroK8s supplies a Helm 3 client through `microk8s helm3`. The repository
wrapper keeps the existing image-build and image-load workflow:

```bash
./scripts/deploy-helm.sh
```

It reads `.env` when present, builds the images, loads or pushes them according
to `K8S_IMAGE_LOAD_MODE`, then executes `helm upgrade --install`. The release
and namespace default to `tfs-agentic`; set `HELM_RELEASE` and
`K8S_NAMESPACE` to change them.

Use `HELM_VALUES_FILE` for settings that should not be represented in `.env`:

```bash
cp devops/helm/tfs-agentic/values.local.example.yaml values.local.yaml
HELM_VALUES_FILE="$PWD/values.local.yaml" ./scripts/deploy-helm.sh
```

`values.local.yaml` is ignored when kept in the chart directory. Never commit
provider API keys or controller credentials.

## Secrets

For a development deployment the wrapper maps the existing `.env` secret
variables into the Helm-created Secret. Helm keeps release values in the
cluster, so production deployments should use an externally managed Secret:

```bash
HELM_EXISTING_SECRET=tfs-agentic-runtime ./scripts/deploy-helm.sh
```

That Secret must contain `TFS_NBI_USER`, `TFS_NBI_PASS`, `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `OPENWEBUI_EMAIL`, and `OPENWEBUI_PASSWORD`.

## Validation and lifecycle

```bash
microk8s helm3 lint devops/helm/tfs-agentic
microk8s helm3 template tfs-agentic devops/helm/tfs-agentic --namespace tfs-agentic
microk8s helm3 status tfs-agentic --namespace tfs-agentic
./scripts/undeploy-helm.sh
```

The chart creates a release-named FAISS PVC by default. Set
`persistence.faiss.existingClaim` when the index must outlive a Helm uninstall.

## Optional Ollama proxy

The host-network Ollama proxy is disabled by default because it is specific to
one environment. To use it, provide `ollama.proxy.enabled`, `hostIP`, and
`targetHost`, and set `config.ollamaBaseUrl` to the generated service address.
