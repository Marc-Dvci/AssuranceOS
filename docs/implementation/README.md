# Implementation inventory

capability-status.yaml is the machine-readable release inventory. Executable
services live in the consolidated src/assuranceos package; top-level service and
connector directories preserve deployment and integration boundaries.

The registry distinguishes executable product capabilities, provider
integrations that activate with credentials, and cloud resource proof supplied
by deployment output.

live-source-collection-plan.md is the plan for the first of those provider
integrations to be run against its real API: one governed collection from the
live GitHub API, over a population measured before the plan was written against
it.
