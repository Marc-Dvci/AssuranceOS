provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_project" "current" {
  project_id = var.project_id
}

locals {
  prefix = "assuranceos-${var.environment}"
  operational_jobs = {
    seed = {
      suffix      = "seed-demo"
      args        = ["scripts/seed_demo_tenant.py", "--model-mode", "vertex"]
      timeout     = "1800s"
      max_retries = 0
    }
    control_test = {
      suffix      = "control-test"
      args        = ["scripts/run_control_test_demo.py"]
      timeout     = "900s"
      max_retries = 0
    }
    scheduler = {
      suffix      = "scheduler"
      args        = ["scripts/run_scheduler_worker.py", "--worker-id", "cloud-run-scheduler"]
      timeout     = "300s"
      max_retries = 1
    }
  }
  services = toset([
    "run.googleapis.com",
    "aiplatform.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "pubsub.googleapis.com",
    "cloudscheduler.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "modelarmor.googleapis.com",
    "cloudtrace.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ])
  database_url         = "postgresql+psycopg://assuranceos:${urlencode(random_password.database.result)}@/assuranceos?host=/cloudsql/${google_sql_database_instance.primary.connection_name}"
  pubsub_service_agent = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_project_service" "required" {
  for_each           = local.services
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "containers" {
  location      = var.region
  repository_id = local.prefix
  description   = "Immutable AssuranceOS release images"
  format        = "DOCKER"

  depends_on = [google_project_service.required]
}

resource "google_service_account" "runtime" {
  account_id   = "${local.prefix}-runtime"
  display_name = "AssuranceOS runtime"
  depends_on   = [google_project_service.required]
}

resource "google_service_account" "scheduler" {
  account_id   = "${local.prefix}-scheduler"
  display_name = "AssuranceOS Cloud Scheduler invoker"
  depends_on   = [google_project_service.required]
}

resource "random_password" "database" {
  length           = 32
  special          = true
  override_special = "-_~"
}

resource "google_sql_database_instance" "primary" {
  name                = "${local.prefix}-postgres"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = var.database_deletion_protection

  settings {
    edition           = "ENTERPRISE"
    tier              = var.database_tier
    availability_type = var.environment == "production" ? "REGIONAL" : "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = 20
    disk_autoresize   = true

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "03:00"
      transaction_log_retention_days = 7

      backup_retention_settings {
        retained_backups = 14
        retention_unit   = "COUNT"
      }
    }

    insights_config {
      query_insights_enabled  = true
      record_application_tags = true
      record_client_address   = false
    }

    # No authorized networks are configured. Cloud Run connects through the
    # Cloud SQL Auth Proxy integration mounted at /cloudsql.
    ip_configuration {
      ipv4_enabled = true
    }

    maintenance_window {
      day          = 7
      hour         = 4
      update_track = "stable"
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_sql_database" "application" {
  name     = "assuranceos"
  instance = google_sql_database_instance.primary.name
}

resource "google_sql_user" "application" {
  name     = "assuranceos"
  instance = google_sql_database_instance.primary.name
  password = random_password.database.result
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "${local.prefix}-database-url"

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = local.database_url
}

resource "google_storage_bucket" "evidence" {
  name                        = "${var.project_id}-${local.prefix}-evidence"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  retention_policy {
    retention_period = var.evidence_retention_seconds
    is_locked        = false
  }

  lifecycle_rule {
    condition {
      age = 365
    }

    action {
      type          = "SetStorageClass"
      storage_class = "ARCHIVE"
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket" "agent_staging" {
  name                        = "${var.project_id}-${local.prefix}-agent-staging"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  lifecycle_rule {
    condition {
      age = 14
    }

    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_pubsub_topic" "outbox" {
  name                       = "${local.prefix}-outbox"
  message_retention_duration = "604800s"
  depends_on                 = [google_project_service.required]
}

resource "google_pubsub_topic" "dead_letter" {
  name                       = "${local.prefix}-dead-letter"
  message_retention_duration = "1209600s"
  depends_on                 = [google_project_service.required]
}

resource "google_pubsub_subscription" "outbox_audit" {
  name                       = "${local.prefix}-outbox-audit"
  topic                      = google_pubsub_topic.outbox.id
  message_retention_duration = "604800s"
  ack_deadline_seconds       = 60

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 10
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

resource "google_project_iam_member" "runtime_sql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_agent_platform" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_model_armor" {
  project = var.project_id
  role    = "roles/modelarmor.user"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# The API enables Cloud Trace and exports spans, so without this every request
# writes a PERMISSION_DENIED on cloudtrace.traces.patch into the log. The first
# deployment did exactly that: an observability claim producing an access error
# on every call is worse than no tracing at all, because the log is the thing an
# auditor reads.
resource "google_project_iam_member" "runtime_trace" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "runtime_agent_staging" {
  bucket = google_storage_bucket.agent_staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "runtime_evidence" {
  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_database" {
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_export_signing" {
  secret_id = var.export_signing_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_execution_signing" {
  secret_id = var.execution_signing_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_evaluator_code" {
  count     = var.evaluator_access_code_secret_id == "" ? 0 : 1
  secret_id = var.evaluator_access_code_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_evaluator_token" {
  count     = var.evaluator_token_secret_id == "" ? 0 : 1
  secret_id = var.evaluator_token_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_pubsub_topic_iam_member" "runtime_publish" {
  topic  = google_pubsub_topic.outbox.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  topic  = google_pubsub_topic.dead_letter.name
  role   = "roles/pubsub.publisher"
  member = local.pubsub_service_agent
}

resource "google_pubsub_subscription_iam_member" "outbox_subscriber" {
  subscription = google_pubsub_subscription.outbox_audit.name
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_service_agent
}

resource "google_cloud_run_v2_service" "api" {
  name                = "${local.prefix}-api"
  location            = var.region
  deletion_protection = var.environment == "production"
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    service_account                  = google_service_account.runtime.email
    timeout                          = "300s"
    max_instance_request_concurrency = 40

    volumes {
      name = "cloudsql"

      cloud_sql_instance {
        instances = [google_sql_database_instance.primary.connection_name]
      }
    }

    volumes {
      name = "export-signing"

      secret {
        secret = var.export_signing_secret_id

        items {
          version = "latest"
          path    = "private.pem"
          # Cloud Run v2 secret volumes are root-owned. The image deliberately
          # runs as a non-root user, so grant read-only access to all identities.
          mode = 0444
        }
      }
    }

    volumes {
      name = "execution-signing"

      secret {
        secret = var.execution_signing_secret_id

        items {
          version = "latest"
          path    = "private.pem"
          # Keep the key immutable while allowing the non-root process to read it.
          mode = 0444
        }
      }
    }

    containers {
      image = var.container_image

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
        cpu_idle = true
      }

      ports {
        container_port = 8080
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      volume_mounts {
        name       = "export-signing"
        mount_path = "/var/run/secrets/export-signing"
      }

      volume_mounts {
        name       = "execution-signing"
        mount_path = "/var/run/secrets/execution-signing"
      }

      env {
        name  = "ASSURANCEOS_ENV"
        value = var.environment == "production" ? "production" : "demo"
      }

      env {
        name  = "ASSURANCEOS_MODEL_MODE"
        value = "vertex"
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }

      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }

      env {
        name  = "ASSURANCEOS_GEMINI_MODEL"
        value = "gemini-3.7-flash"
      }

      env {
        name  = "ASSURANCEOS_MODEL_ARMOR_TEMPLATE"
        value = var.model_armor_template
      }

      env {
        name  = "ASSURANCEOS_AGENT_ENGINE_STAGING_BUCKET"
        value = "gs://${google_storage_bucket.agent_staging.name}"
      }

      env {
        name  = "ASSURANCEOS_AGENT_ENGINE_RESOURCE_MAP_JSON"
        value = var.agent_engine_resource_map_json
      }

      env {
        name  = "ASSURANCEOS_AUTH_MODE"
        value = "jwt"
      }

      env {
        name  = "ASSURANCEOS_AUTH_JWT_ISSUER"
        value = var.auth_jwt_issuer
      }

      env {
        name  = "ASSURANCEOS_AUTH_JWT_AUDIENCE"
        value = var.auth_jwt_audience
      }

      env {
        name  = "ASSURANCEOS_AUTH_JWKS_URL"
        value = var.auth_jwks_url
      }

      env {
        name  = "ASSURANCEOS_AUTH_JWT_ALGORITHMS"
        value = "RS256"
      }

      env {
        name  = "ASSURANCEOS_TRUSTED_HOSTS"
        value = var.trusted_hosts
      }

      env {
        name  = "ASSURANCEOS_AUTO_CREATE_SCHEMA"
        value = "false"
      }

      env {
        name  = "ASSURANCEOS_EVIDENCE_STORAGE"
        value = "gcs"
      }

      env {
        name  = "ASSURANCEOS_EVIDENCE_BUCKET"
        value = google_storage_bucket.evidence.name
      }

      env {
        name  = "ASSURANCEOS_AGENT_ROOT"
        value = "/app/agents"
      }

      env {
        name  = "ASSURANCEOS_DEMO_ROOT"
        value = "/app/demo/asteria"
      }

      env {
        name  = "ASSURANCEOS_EXPORT_SIGNING_KEY_ID"
        value = "${local.prefix}-exports-v1"
      }

      env {
        name  = "ASSURANCEOS_EXPORT_SIGNING_PRIVATE_KEY"
        value = "/var/run/secrets/export-signing/private.pem"
      }

      env {
        name  = "ASSURANCEOS_EXECUTION_SIGNING_KEY_ID"
        value = "assuranceos-execution-v1"
      }

      env {
        name  = "ASSURANCEOS_EXECUTION_SIGNING_PRIVATE_KEY"
        value = "/var/run/secrets/execution-signing/private.pem"
      }

      env {
        name = "ASSURANCEOS_DATABASE_URL"

        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = google_secret_manager_secret_version.database_url.version
          }
        }
      }

      # The evaluator access code and the token it returns are both credentials,
      # so neither is a plain env value on the revision. They are optional: an
      # empty `evaluator_access_code_secret_id` leaves the exchange endpoint
      # returning 404 and the workspace reachable only by pasting a token.
      dynamic "env" {
        for_each = var.evaluator_access_code_secret_id == "" ? [] : [1]

        content {
          name = "ASSURANCEOS_EVALUATOR_ACCESS_CODE"

          value_source {
            secret_key_ref {
              secret  = var.evaluator_access_code_secret_id
              version = "latest"
            }
          }
        }
      }

      dynamic "env" {
        for_each = var.evaluator_token_secret_id == "" ? [] : [1]

        content {
          name = "ASSURANCEOS_EVALUATOR_TOKEN"

          value_source {
            secret_key_ref {
              secret  = var.evaluator_token_secret_id
              version = "latest"
            }
          }
        }
      }

      # Both probes send an explicit Host header, and it is not decoration.
      #
      # The application runs TrustedHostMiddleware and production refuses a
      # wildcard allowlist on purpose. Cloud Run addresses the container by its
      # instance address, so a probe's Host header is an internal IP — which the
      # middleware rejects with 400 "Invalid host header" while the process is
      # perfectly healthy. The first deployment of this module failed exactly
      # that way: twelve consecutive startup probes refused, the revision never
      # served traffic, and the logs showed `Application startup complete`
      # directly above the refusals. So the probe identifies itself with a name
      # the deployment can allowlist, and `trusted_hosts` must contain
      # `probe_host`.
      startup_probe {
        initial_delay_seconds = 5
        timeout_seconds       = 5
        period_seconds        = 5
        failure_threshold     = 12

        http_get {
          # Startup proves the process is alive. Deployment migrations and
          # registry synchronization are separately visible through /ready.
          path = "/health"
          port = 8080

          http_headers {
            name  = "Host"
            value = var.probe_host
          }
        }
      }

      liveness_probe {
        timeout_seconds   = 5
        period_seconds    = 30
        failure_threshold = 3

        http_get {
          path = "/health"
          port = 8080

          http_headers {
            name  = "Host"
            value = var.probe_host
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_project_iam_member.runtime_sql,
    google_project_iam_member.runtime_agent_platform,
    google_project_iam_member.runtime_model_armor,
    google_secret_manager_secret_iam_member.runtime_database,
    google_secret_manager_secret_iam_member.runtime_export_signing,
    google_secret_manager_secret_iam_member.runtime_execution_signing,
    google_storage_bucket_iam_member.runtime_evidence,
    google_storage_bucket_iam_member.runtime_agent_staging,
  ]
}

resource "google_cloud_run_v2_job" "migrate" {
  name                = "${local.prefix}-migrate"
  location            = var.region
  deletion_protection = var.environment == "production"

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.runtime.email
      timeout         = "900s"
      max_retries     = 1

      volumes {
        name = "cloudsql"

        cloud_sql_instance {
          instances = [google_sql_database_instance.primary.connection_name]
        }
      }

      containers {
        image   = var.container_image
        command = ["sh"]
        args    = ["-c", "python scripts/migrate.py && python scripts/sync_control_test_registry.py"]

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }

        env {
          name  = "ASSURANCEOS_ENV"
          value = var.environment == "production" ? "production" : "demo"
        }

        env {
          name  = "ASSURANCEOS_AUTH_MODE"
          value = "jwt"
        }

        env {
          name  = "ASSURANCEOS_AUTH_JWT_ISSUER"
          value = var.auth_jwt_issuer
        }

        env {
          name  = "ASSURANCEOS_AUTH_JWT_AUDIENCE"
          value = var.auth_jwt_audience
        }

        env {
          name  = "ASSURANCEOS_AUTH_JWKS_URL"
          value = var.auth_jwks_url
        }
        # Not optional, and not cosmetic: the algorithm list defaults to HS256
        # and Settings.validate refuses an HS algorithm alongside a JWKS URL, so
        # a job configured with the URL and without this crashes on import,
        # before it opens the database. The API service and the operations jobs
        # already set it; these two did not, and the first deployment's
        # migration failed with "JWKS verification cannot use HS algorithms".
        env {
          name  = "ASSURANCEOS_AUTH_JWT_ALGORITHMS"
          value = "RS256"
        }

        env {
          name  = "ASSURANCEOS_AUTO_CREATE_SCHEMA"
          value = "false"
        }

        env {
          name = "ASSURANCEOS_DATABASE_URL"

          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.secret_id
              version = google_secret_manager_secret_version.database_url.version
            }
          }
        }
      }
    }
  }

  depends_on = [google_project_iam_member.runtime_sql]
}

resource "google_cloud_run_v2_job" "outbox" {
  name                = "${local.prefix}-outbox"
  location            = var.region
  deletion_protection = var.environment == "production"

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.runtime.email
      timeout         = "300s"
      max_retries     = 1

      volumes {
        name = "cloudsql"

        cloud_sql_instance {
          instances = [google_sql_database_instance.primary.connection_name]
        }
      }

      containers {
        image   = var.container_image
        command = ["python"]
        args = [
          "scripts/run_outbox_dispatcher.py",
          "--worker-id",
          "cloud-run-outbox",
          "--project-id",
          var.project_id,
          "--topic-id",
          google_pubsub_topic.outbox.name,
        ]

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }

        env {
          name  = "ASSURANCEOS_ENV"
          value = var.environment == "production" ? "production" : "demo"
        }

        env {
          name  = "ASSURANCEOS_AUTH_MODE"
          value = "jwt"
        }

        env {
          name  = "ASSURANCEOS_AUTH_JWT_ISSUER"
          value = var.auth_jwt_issuer
        }

        env {
          name  = "ASSURANCEOS_AUTH_JWT_AUDIENCE"
          value = var.auth_jwt_audience
        }

        env {
          name  = "ASSURANCEOS_AUTH_JWKS_URL"
          value = var.auth_jwks_url
        }
        # Not optional, and not cosmetic: the algorithm list defaults to HS256
        # and Settings.validate refuses an HS algorithm alongside a JWKS URL, so
        # a job configured with the URL and without this crashes on import,
        # before it opens the database. The API service and the operations jobs
        # already set it; these two did not, and the first deployment's
        # migration failed with "JWKS verification cannot use HS algorithms".
        env {
          name  = "ASSURANCEOS_AUTH_JWT_ALGORITHMS"
          value = "RS256"
        }

        env {
          name  = "ASSURANCEOS_AUTO_CREATE_SCHEMA"
          value = "false"
        }

        env {
          name = "ASSURANCEOS_DATABASE_URL"

          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.secret_id
              version = google_secret_manager_secret_version.database_url.version
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.runtime_sql,
    google_pubsub_topic_iam_member.runtime_publish,
  ]
}

resource "google_cloud_run_v2_job" "operations" {
  for_each            = local.operational_jobs
  name                = "${local.prefix}-${each.value.suffix}"
  location            = var.region
  deletion_protection = var.environment == "production"

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.runtime.email
      timeout         = each.value.timeout
      max_retries     = each.value.max_retries

      volumes {
        name = "cloudsql"

        cloud_sql_instance {
          instances = [google_sql_database_instance.primary.connection_name]
        }
      }

      volumes {
        name = "export-signing"

        secret {
          secret = var.export_signing_secret_id

          items {
            version = "latest"
            path    = "private.pem"
            mode    = 0444
          }
        }
      }

      volumes {
        name = "execution-signing"

        secret {
          secret = var.execution_signing_secret_id

          items {
            version = "latest"
            path    = "private.pem"
            mode    = 0444
          }
        }
      }

      containers {
        image   = var.container_image
        command = ["python"]
        args    = each.value.args

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }

        volume_mounts {
          name       = "export-signing"
          mount_path = "/var/run/secrets/export-signing"
        }

        volume_mounts {
          name       = "execution-signing"
          mount_path = "/var/run/secrets/execution-signing"
        }

        env {
          name  = "ASSURANCEOS_ENV"
          value = var.environment == "production" ? "production" : "demo"
        }

        env {
          name  = "ASSURANCEOS_MODEL_MODE"
          value = "vertex"
        }

        env {
          name  = "ASSURANCEOS_GEMINI_MODEL"
          value = "gemini-3.7-flash"
        }

        env {
          name  = "ASSURANCEOS_MODEL_ARMOR_TEMPLATE"
          value = var.model_armor_template
        }

        env {
          name  = "GOOGLE_CLOUD_PROJECT"
          value = var.project_id
        }

        env {
          name  = "GOOGLE_CLOUD_LOCATION"
          value = var.region
        }

        env {
          name  = "ASSURANCEOS_AUTH_MODE"
          value = "jwt"
        }

        env {
          name  = "ASSURANCEOS_AUTH_JWT_ISSUER"
          value = var.auth_jwt_issuer
        }

        env {
          name  = "ASSURANCEOS_AUTH_JWT_AUDIENCE"
          value = var.auth_jwt_audience
        }

        env {
          name  = "ASSURANCEOS_AUTH_JWKS_URL"
          value = var.auth_jwks_url
        }

        env {
          name  = "ASSURANCEOS_AUTH_JWT_ALGORITHMS"
          value = "RS256"
        }

        env {
          name  = "ASSURANCEOS_TRUSTED_HOSTS"
          value = var.trusted_hosts
        }

        env {
          name  = "ASSURANCEOS_AUTO_CREATE_SCHEMA"
          value = "false"
        }

        env {
          name  = "ASSURANCEOS_EVIDENCE_STORAGE"
          value = "gcs"
        }

        env {
          name  = "ASSURANCEOS_EVIDENCE_BUCKET"
          value = google_storage_bucket.evidence.name
        }

        env {
          name  = "ASSURANCEOS_EXPORT_SIGNING_KEY_ID"
          value = "${local.prefix}-exports-v1"
        }

        env {
          name  = "ASSURANCEOS_EXPORT_SIGNING_PRIVATE_KEY"
          value = "/var/run/secrets/export-signing/private.pem"
        }

        env {
          name  = "ASSURANCEOS_EXECUTION_SIGNING_KEY_ID"
          value = "assuranceos-execution-v1"
        }

        env {
          name  = "ASSURANCEOS_EXECUTION_SIGNING_PRIVATE_KEY"
          value = "/var/run/secrets/execution-signing/private.pem"
        }

        env {
          name = "ASSURANCEOS_DATABASE_URL"

          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.secret_id
              version = google_secret_manager_secret_version.database_url.version
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_iam_member.runtime_sql,
    google_project_iam_member.runtime_agent_platform,
    google_project_iam_member.runtime_model_armor,
    google_secret_manager_secret_iam_member.runtime_database,
    google_secret_manager_secret_iam_member.runtime_export_signing,
    google_secret_manager_secret_iam_member.runtime_execution_signing,
    google_storage_bucket_iam_member.runtime_evidence,
  ]
}

resource "google_project_iam_member" "scheduler_run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "outbox" {
  name        = "${local.prefix}-outbox-dispatch"
  description = "Dispatch the AssuranceOS transactional outbox"
  region      = var.region
  schedule    = var.outbox_dispatch_schedule
  time_zone   = "UTC"

  retry_config {
    retry_count          = 3
    min_backoff_duration = "10s"
    max_backoff_duration = "60s"
    max_doublings        = 3
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.outbox.name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_project_iam_member.scheduler_run_invoker]
}

resource "google_cloud_scheduler_job" "audit_scheduler" {
  name        = "${local.prefix}-audit-scheduler"
  description = "Evaluate due AssuranceOS schedules and launch eligible engagements"
  region      = var.region
  schedule    = var.audit_scheduler_schedule
  time_zone   = "UTC"

  retry_config {
    retry_count          = 3
    min_backoff_duration = "10s"
    max_backoff_duration = "60s"
    max_doublings        = 3
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.operations["scheduler"].name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_project_iam_member.scheduler_run_invoker]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.allow_unauthenticated_cloud_run ? 1 : 0
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
