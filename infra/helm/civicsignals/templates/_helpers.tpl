{{/*
CivicSignals Helm chart — template helpers
task O3 / doc 06 §10
SPDX-License-Identifier: AGPL-3.0-only
*/}}

{{/*
Expand the name of the chart.
*/}}
{{- define "civicsignals.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
Truncates at 63 chars to comply with DNS naming spec.
*/}}
{{- define "civicsignals.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart label.
*/}}
{{- define "civicsignals.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels (all resources)
*/}}
{{- define "civicsignals.labels" -}}
helm.sh/chart: {{ include "civicsignals.chart" . }}
{{ include "civicsignals.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels — used in matchLabels + Service selector
*/}}
{{- define "civicsignals.selectorLabels" -}}
app.kubernetes.io/name: {{ include "civicsignals.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Service account name
*/}}
{{- define "civicsignals.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "civicsignals.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Resolved API image (repo + tag).  Tag falls back to .Chart.AppVersion.
*/}}
{{- define "civicsignals.apiImage" -}}
{{- $tag := .Values.image.api.tag | default .Chart.AppVersion }}
{{- printf "%s:%s" .Values.image.api.repository $tag }}
{{- end }}

{{/*
Resolved Web image (repo + tag).  Tag falls back to .Chart.AppVersion.
*/}}
{{- define "civicsignals.webImage" -}}
{{- $tag := .Values.image.web.tag | default .Chart.AppVersion }}
{{- printf "%s:%s" .Values.image.web.repository $tag }}
{{- end }}

{{/*
Standard environment references injected into every API-family container.
Pulls from the Secret and ConfigMap created by this chart.
*/}}
{{- define "civicsignals.apiEnv" -}}
# --- from Secret ---
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: DATABASE_URL
- name: DATABASE_DIRECT_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: DATABASE_DIRECT_URL
- name: REDIS_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: REDIS_URL
- name: S3_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: S3_ACCESS_KEY_ID
- name: S3_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: S3_SECRET_ACCESS_KEY
- name: SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: SECRET_KEY
- name: ANTHROPIC_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: ANTHROPIC_API_KEY
- name: OPENAI_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: OPENAI_API_KEY
- name: SMTP_HOST
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: SMTP_HOST
- name: SMTP_PORT
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: SMTP_PORT
- name: SMTP_USER
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: SMTP_USER
- name: SMTP_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: SMTP_PASSWORD
- name: SMTP_FROM_EMAIL
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: SMTP_FROM_EMAIL
- name: SENTRY_DSN
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: SENTRY_DSN
# --- from ConfigMap ---
- name: S3_ENDPOINT
  valueFrom:
    configMapKeyRef:
      name: {{ include "civicsignals.fullname" . }}-config
      key: S3_ENDPOINT
- name: S3_BUCKET
  valueFrom:
    configMapKeyRef:
      name: {{ include "civicsignals.fullname" . }}-config
      key: S3_BUCKET
- name: S3_REGION
  valueFrom:
    configMapKeyRef:
      name: {{ include "civicsignals.fullname" . }}-config
      key: S3_REGION
- name: LOG_LEVEL
  valueFrom:
    configMapKeyRef:
      name: {{ include "civicsignals.fullname" . }}-config
      key: LOG_LEVEL
- name: ENABLE_SENTRY
  valueFrom:
    configMapKeyRef:
      name: {{ include "civicsignals.fullname" . }}-config
      key: ENABLE_SENTRY
- name: ENABLE_TELEMETRY
  valueFrom:
    configMapKeyRef:
      name: {{ include "civicsignals.fullname" . }}-config
      key: ENABLE_TELEMETRY
- name: API_BASE_URL
  valueFrom:
    configMapKeyRef:
      name: {{ include "civicsignals.fullname" . }}-config
      key: API_BASE_URL
- name: WEB_BASE_URL
  valueFrom:
    configMapKeyRef:
      name: {{ include "civicsignals.fullname" . }}-config
      key: WEB_BASE_URL
{{- end }}
