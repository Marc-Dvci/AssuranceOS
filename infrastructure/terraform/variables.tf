variable "project_id" {
  description = "Google Cloud project hosting AssuranceOS."
  type        = string
}

variable "region" {
  description = "Primary Google Cloud region."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment suffix and runtime environment name."
  type        = string
  default     = "demo"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,15}$", var.environment))
    error_message = "environment must be a short lowercase identifier"
  }
}

variable "container_image" {
  description = "Immutable Artifact Registry image digest, not a mutable tag."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.container_image))
    error_message = "container_image must be pinned by sha256 digest"
  }
}

variable "auth_jwt_issuer" {
  description = "OIDC issuer accepted by the application JWT verifier."
  type        = string
}

variable "auth_jwt_audience" {
  description = "Required application JWT audience."
  type        = string
}

variable "auth_jwks_url" {
  description = "HTTPS JWKS endpoint for the configured OIDC issuer."
  type        = string

  validation {
    condition     = startswith(var.auth_jwks_url, "https://")
    error_message = "auth_jwks_url must use HTTPS"
  }
}

variable "trusted_hosts" {
  description = "Comma-separated application host allowlist. Must include var.probe_host, or Cloud Run's health probes are refused by TrustedHostMiddleware and no revision ever serves traffic."
  type        = string

  validation {
    condition     = can(regex("(^|,)\\s*cloudrun\\.probe\\.internal\\s*($|,)", var.trusted_hosts)) || var.probe_host != "cloudrun.probe.internal"
    error_message = "trusted_hosts must include the probe host (default cloudrun.probe.internal); the health probes send it as their Host header"
  }
}

variable "probe_host" {
  description = "Host header the Cloud Run startup and liveness probes send. It exists because the probes otherwise address the container by its instance IP, which the application's trusted-host allowlist refuses."
  type        = string
  default     = "cloudrun.probe.internal"
}

variable "export_signing_secret_id" {
  description = "Existing Secret Manager secret containing an Ed25519 private key PEM. The key is mounted into the API and is never supplied through Terraform state."
  type        = string
}

variable "execution_signing_secret_id" {
  description = "Existing Secret Manager secret containing the Ed25519 control-plane private key used to issue short-lived execution envelopes. Key material is never supplied through Terraform state."
  type        = string
}

variable "model_armor_template" {
  description = "Existing Model Armor template resource used to sanitize prompts and responses. Leave empty until the template has been created."
  type        = string
  default     = ""

  validation {
    condition     = var.model_armor_template == "" || can(regex("^projects/[^/]+/locations/[^/]+/templates/[^/]+$", var.model_armor_template))
    error_message = "model_armor_template must be a full projects/.../locations/.../templates/... resource name"
  }
}

variable "agent_engine_resource_map_json" {
  description = "Deployment result JSON emitted by scripts/deploy_adk_agent.py after live Agent Engine read-back. Empty means no cloud fleet is claimed."
  type        = string
  default     = ""

  validation {
    condition     = var.agent_engine_resource_map_json == "" || can(jsondecode(var.agent_engine_resource_map_json))
    error_message = "agent_engine_resource_map_json must be empty or valid JSON"
  }
}

variable "database_tier" {
  description = "Cloud SQL Enterprise tier."
  type        = string
  default     = "db-custom-1-3840"
}

variable "database_deletion_protection" {
  description = "Protect the Cloud SQL instance from accidental deletion."
  type        = bool
  default     = true
}

variable "evidence_retention_seconds" {
  description = "Minimum Cloud Storage retention for immutable evidence objects."
  type        = number
  default     = 31536000

  validation {
    condition     = var.evidence_retention_seconds >= 86400
    error_message = "evidence retention must be at least one day"
  }
}

variable "allow_unauthenticated_cloud_run" {
  description = "Expose Cloud Run publicly; application JWT authorization still applies."
  type        = bool
  default     = false
}

variable "min_instances" {
  type    = number
  default = 0

  validation {
    condition     = var.min_instances >= 0
    error_message = "min_instances cannot be negative"
  }
}

variable "max_instances" {
  type    = number
  default = 5

  validation {
    condition     = var.max_instances >= 1 && var.max_instances >= var.min_instances
    error_message = "max_instances must be at least one and not lower than min_instances"
  }
}
