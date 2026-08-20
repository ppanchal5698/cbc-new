# S3: the immutable source bucket and the high-volume derived bucket (§11.3).
#
# The source bucket holds client bid documents. It is written once, at intake, and
# never again — that immutability is what makes a citation from six months ago
# still mean something, and it is the reason the pipeline can re-run without any
# risk of the underlying document having changed.

variable "name_prefix" {
  type = string
}

variable "object_lock_retention_days" {
  description = <<-EOT
    Default Object Lock retention, in days.

    **Left null until CBC signs the retention period off in writing** (§11.3).
    Object Lock cannot be disabled once a bucket is created with it enabled, and a
    default retention set too long cannot be shortened for objects already written.
    A guessed number here is a decade of storage nobody agreed to pay for, or a
    legal-hold posture nobody agreed to adopt.

    Versioning, the lock configuration on the bucket, and GOVERNANCE mode are all
    in place — only the default duration is deferred, so switching it on later is a
    one-line change and not a bucket migration.
  EOT
  type        = number
  default     = null
}

variable "derived_transition_days" {
  description = "Days before derived artefacts move to Glacier Instant Retrieval."
  type        = number
  default     = 90
}

variable "enable_intelligent_tiering" {
  type    = bool
  default = true
}

# ── source ───────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "source" {
  bucket = "${var.name_prefix}-source"

  # Must be set at creation. It cannot be turned on later, which is why it is
  # enabled here even though the default retention period is still deferred.
  object_lock_enabled = true

  lifecycle {
    prevent_destroy = true
  }
}

# Object Lock requires versioning, and enabling lock without it is the apply-time
# error the previous configuration hit.
resource "aws_s3_bucket_versioning" "source" {
  bucket = aws_s3_bucket.source.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "source" {
  # Created only once a retention period has been agreed. Until then the bucket is
  # lock-enabled with no default rule: objects can still be locked per-object, and
  # nothing is silently retained for ten years on an unreviewed guess.
  count  = var.object_lock_retention_days == null ? 0 : 1
  bucket = aws_s3_bucket.source.id

  rule {
    default_retention {
      # GOVERNANCE, never COMPLIANCE. COMPLIANCE cannot be overridden by anyone,
      # including the root account, for the whole retention period — a mistaken
      # upload would be unremovable. GOVERNANCE gives the same protection with a
      # documented, alarmed break-glass path (see the ai/observability modules).
      mode = "GOVERNANCE"
      days = var.object_lock_retention_days
    }
  }

  depends_on = [aws_s3_bucket_versioning.source]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "source" {
  bucket = aws_s3_bucket.source.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    # Halves the KMS-free request cost at high object counts and costs nothing.
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "source" {
  bucket                  = aws_s3_bucket.source.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── derived ──────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "derived" {
  bucket = "${var.name_prefix}-derived"
}

resource "aws_s3_bucket_versioning" "derived" {
  bucket = aws_s3_bucket.derived.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "derived" {
  bucket = aws_s3_bucket.derived.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "derived" {
  bucket                  = aws_s3_bucket.derived.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "derived" {
  bucket = aws_s3_bucket.derived.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "tier-cold-artefacts"
    status = "Enabled"
    filter {}
    transition {
      days          = var.derived_transition_days
      storage_class = "GLACIER_IR"
    }
  }

  # Page rasters are regenerable from the immutable source at any time, so old
  # non-current versions are pure cost.
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# Viewer rasters are hot for a week while a bid is reviewed and then never touched
# again. Intelligent-Tiering moves them without anyone predicting when.
resource "aws_s3_bucket_intelligent_tiering_configuration" "derived" {
  count  = var.enable_intelligent_tiering ? 1 : 0
  bucket = aws_s3_bucket.derived.id
  name   = "entire-bucket"

  tiering {
    access_tier = "ARCHIVE_ACCESS"
    days        = 90
  }
}

output "source_bucket" {
  value = aws_s3_bucket.source.id
}

output "source_bucket_arn" {
  value = aws_s3_bucket.source.arn
}

output "derived_bucket" {
  value = aws_s3_bucket.derived.id
}

output "derived_bucket_arn" {
  value = aws_s3_bucket.derived.arn
}

output "derived_bucket_regional_domain_name" {
  value = aws_s3_bucket.derived.bucket_regional_domain_name
}

output "object_lock_retention_configured" {
  description = "False until CBC signs off the retention period. Surfaced so a deploy cannot quietly ship without it."
  value       = var.object_lock_retention_days != null
}
