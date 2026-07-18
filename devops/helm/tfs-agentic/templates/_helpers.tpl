{{- define "tfs-agentic.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "tfs-agentic.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := include "tfs-agentic.name" . }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "tfs-agentic.agentName" -}}
{{- printf "%s-agent" (include "tfs-agentic.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "tfs-agentic.mcpName" -}}
{{- printf "%s-mcp" (include "tfs-agentic.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "tfs-agentic.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: tfs-agentic
{{- end }}

{{- define "tfs-agentic.agentSelectorLabels" -}}
app.kubernetes.io/name: {{ include "tfs-agentic.agentName" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "tfs-agentic.mcpSelectorLabels" -}}
app.kubernetes.io/name: {{ include "tfs-agentic.mcpName" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "tfs-agentic.secretName" -}}
{{- default (printf "%s-secret" (include "tfs-agentic.fullname" .)) .Values.secrets.existingSecret }}
{{- end }}
