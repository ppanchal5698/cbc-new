# ------------------------------------------------------------------------------
# SOURCE BUCKET - High Security / Immutable
# ------------------------------------------------------------------------------

resource "aws_s3_bucket" "source_bucket" {
  bucket = "cbc-copilot-source-${var.environment}"
  
  # Protect against accidental deletion
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "source_versioning" {
  bucket = aws_s3_bucket.source_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "source_lock" {
  bucket = aws_s3_bucket.source_bucket.id

  # Section 11.3 & Defect B17: Explicitly GOVERNANCE mode, not Compliance.
  rule {
    default_retention {
      mode  = "GOVERNANCE"
      days  = 3650 # 10 years
    }
  }
}

# ------------------------------------------------------------------------------
# DERIVED BUCKET - High Volume / Tiered
# ------------------------------------------------------------------------------

resource "aws_s3_bucket" "derived_bucket" {
  bucket = "cbc-copilot-derived-${var.environment}"
}

resource "aws_s3_bucket_versioning" "derived_versioning" {
  bucket = aws_s3_bucket.derived_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "derived_lifecycle" {
  bucket = aws_s3_bucket.derived_bucket.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    
    filter {}
    
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "tier-to-glacier-ir"
    status = "Enabled"
    
    filter {}
    
    transition {
      days          = 90
      storage_class = "GLACIER_IR"
    }
  }
}
