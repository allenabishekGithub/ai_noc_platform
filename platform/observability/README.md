# Observability platform

This directory provides the observability baseline for the TFS Agentic Module:

- Prometheus scrapes `/metrics` from the agent, MCP server, and collector.
- Grafana discovers the four dashboard ConfigMaps generated from JSON files.
- OpenTelemetry traces are sent from the agent and MCP server to the collector,
  then forwarded to Tempo.
- The collector is configured to forward OTLP logs to Loki. Kubernetes stdout
  log collection still requires a node-level log agent such as Grafana Alloy;
  add that before relying on Loki for application logs.

## Deploy the backends on MicroK8s

```bash
microk8s kubectl create namespace observability --dry-run=client -o yaml | microk8s kubectl apply -f -

microk8s helm3 repo add prometheus-community https://prometheus-community.github.io/helm-charts
microk8s helm3 repo add grafana https://grafana.github.io/helm-charts
microk8s helm3 repo update

microk8s helm3 upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace observability \
  --values platform/observability/prometheus/values.yaml \
  --wait --timeout 10m

microk8s helm3 upgrade --install loki grafana/loki \
  --namespace observability \
  --values platform/observability/loki/values.yaml \
  --wait --timeout 10m

microk8s helm3 upgrade --install tempo grafana/tempo \
  --namespace observability \
  --values platform/observability/tempo/values.yaml \
  --wait --timeout 10m

microk8s kubectl apply -k platform/observability
```

Apply the final `kustomize` command only after `kube-prometheus-stack` is
ready, because it creates `ServiceMonitor` custom resources.

## Enable tracing in the GitOps application

`devops/helm/tfs-agentic/values.gitops.yaml` configures both workloads with
the collector's OTLP gRPC endpoint. Build and push images that include the new
instrumentation, then commit an immutable image tag in that values file. Argo
CD will reconcile the rollout from Git.

Metrics are independent of tracing: `/metrics` remains available even when
`OTEL_EXPORTER_OTLP_ENDPOINT` is empty.

## Verify

```bash
microk8s kubectl -n tfs-agentic port-forward service/tfs-agentic-agent 8800:8800
curl http://127.0.0.1:8800/metrics

microk8s kubectl -n observability get servicemonitors
microk8s kubectl -n observability port-forward service/kube-prometheus-stack-grafana 3000:80
```

The Grafana dashboard ConfigMap labels are compatible with the dashboard
sidecar configured in `prometheus/values.yaml`.
