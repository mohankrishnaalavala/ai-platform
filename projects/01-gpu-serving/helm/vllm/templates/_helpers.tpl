{{- define "vllm.labels" -}}
{{- range $k, $v := .Values.labels }}
{{ $k }}: {{ required (printf "labels.%s is required" $k) $v | quote }}
{{- end }}
{{- end -}}
