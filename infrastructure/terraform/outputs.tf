output "api_uri" {
  value = google_cloud_run_v2_service.api.uri
}

output "evidence_bucket" {
  value = google_storage_bucket.evidence.name
}

output "agent_engine_staging_bucket" {
  value = google_storage_bucket.agent_staging.url
}

output "cloud_sql_connection_name" {
  value = google_sql_database_instance.primary.connection_name
}

output "migration_job" {
  value = google_cloud_run_v2_job.migrate.name
}

output "outbox_topic" {
  value = google_pubsub_topic.outbox.id
}

output "outbox_dispatch_job" {
  value = google_cloud_run_v2_job.outbox.name
}
