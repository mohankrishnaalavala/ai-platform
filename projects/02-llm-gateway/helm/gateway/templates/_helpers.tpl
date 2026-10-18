{{- define "gateway.labels" -}}
{{- range $k, $v := .Values.labels }}
{{ $k }}: {{ required (printf "labels.%s is required" $k) $v | quote }}
{{- end }}
{{- end -}}

{{/* A Secret Manager secret's latest version, mounted as a file named after the secret. */}}
{{- define "gateway.secret" -}}
- resourceName: "projects/{{ required "project is required" .project }}/secrets/{{ .name }}/versions/latest"
  path: {{ .name | quote }}
{{- end -}}
