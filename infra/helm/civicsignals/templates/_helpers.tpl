{{/*
CivicSignals Helm chart — template helpers
task O3 / doc 06 §10
Env var names align with Settings in apps/api/src/civicsignals_api/config.py.
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
Env var names match Settings in apps/api/src/civicsignals_api/config.py.
Pulls from the Secret and ConfigMap created by this chart.
*/}}
{{- define "civicsignals.apiEnv" -}}
# --- from Secret (sensitive values) ---
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
- name: CELERY_BROKER_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: CELERY_BROKER_URL
- name: CELERY_RESULT_BACKEND
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: CELERY_RESULT_BACKEND
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
# Settings.email_from (env var EMAIL_FROM)
- name: EMAIL_FROM
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: EMAIL_FROM
- name: SENTRY_DSN
  valueFrom:
    secretKeyRef:
      name: {{ include "civicsignals.fullname" . }}-secret
      key: SENTRY_DSN
# --- from ConfigMap (non-sensitive config) ---
# Settings.s3_endpoint_url
- name: S3_ENDPOINT_URL
  valueFrom:
    configMapKeyRef:
      name: {{ include "civicsignals.fullname" . }}-config
      key: S3_ENDPOINT_URL
# Settings.s3_raw_bucket
- name: S3_RAW_BUCKET
  valueFrom:
    configMapKeyRef:
      name: {{ include "civicsignals.fullname" . }}-config
      key: S3_RAW_BUCKET
# Settings.s3_region
- name: S3_REGION
  valueFrom:
    configMapKeyRef:
      name: {{ include "civicsignals.fullname" . }}-config
      key: S3_REGION
# Settings.log_level
- name: LOG_LEVEL
  valueFrom:
    configMapKeyRef:
      name: {{ include "civicsignals.fullname" . }}-config
      key: LOG_LEVEL
# Settings.environment
- name: ENVIRONMENT
  valueFrom:
    configMapKeyRef:
      name: {{ include "civicsignals.fullname" . }}-config
      key: ENVIRONMENT
{{- end }}
