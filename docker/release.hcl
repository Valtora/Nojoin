# The four release images, built together by scripts/release.py on the
# maintainer's release host. The script sets every variable below; building
# this file by hand is only useful for debugging it.
#
# worker-io is built FROM the worker target in the same graph, so it layers
# onto exactly the worker this run produced without that worker being pushed
# first. The compose files do the same through the worker_base context.

variable "IMAGE_BASE" {
  default = "ghcr.io/valtora/nojoin"
}

variable "VERSION" {
  default = ""
}

# Full commit SHA the images are built from. release.yml refuses to sign an
# image whose revision label does not match the tag's commit.
variable "REVISION" {
  default = ""
}

# Commit time in RFC 3339. Paired with SOURCE_DATE_EPOCH in the environment,
# it keeps the image config identical between the scan build and the push
# build, which is what lets the script prove it pushed what it scanned.
variable "CREATED" {
  default = ""
}

function "release_tags" {
  params = [suffix]
  result = [
    "${IMAGE_BASE}${suffix}:${VERSION}",
    "${IMAGE_BASE}${suffix}:sha-${substr(REVISION, 0, 7)}",
  ]
}

group "default" {
  targets = ["api", "worker", "worker-io", "frontend"]
}

target "_release" {
  platforms = ["linux/amd64"]
  labels = {
    "org.opencontainers.image.created"     = CREATED
    "org.opencontainers.image.description" = "Open-source self-hosted meeting transcription software with diarisation and more."
    "org.opencontainers.image.licenses"    = "NOASSERTION"
    "org.opencontainers.image.revision"    = REVISION
    "org.opencontainers.image.source"      = "https://github.com/Valtora/Nojoin"
    "org.opencontainers.image.title"       = "Nojoin"
    "org.opencontainers.image.url"         = "https://github.com/Valtora/Nojoin"
    "org.opencontainers.image.version"     = VERSION
  }
}

target "api" {
  inherits   = ["_release"]
  context    = "."
  dockerfile = "docker/Dockerfile.api"
  args = {
    NOJOIN_SERVER_VERSION = VERSION
  }
  tags = release_tags("-api")
}

target "worker" {
  inherits   = ["_release"]
  context    = "."
  dockerfile = "docker/Dockerfile.worker"
  tags       = release_tags("-worker")
}

target "worker-io" {
  inherits   = ["_release"]
  context    = "."
  dockerfile = "docker/Dockerfile.worker-io"
  contexts = {
    worker_base = "target:worker"
  }
  args = {
    WORKER_BASE_IMAGE = "worker_base"
  }
  tags = release_tags("-worker-io")
}

target "frontend" {
  inherits   = ["_release"]
  context    = "frontend"
  dockerfile = "Dockerfile"
  args = {
    NEXT_PUBLIC_API_URL = "/api"
  }
  tags = release_tags("-frontend")
}
