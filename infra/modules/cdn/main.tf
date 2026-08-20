# CloudFront over the derived bucket (§4.5, bottleneck B5).
#
# Page rasters are rendered once at ingest and served from here. The design this
# replaces rendered a PDF region with PyMuPDF on every click: a 1-3 second CPU and
# memory spike on the same host serving every estimator, for the single most
# clicked feature in the review UI. Inverting it turns a CPU-bound Python call
# into a CDN GET, and it is what makes 8 GiB sufficient for the API host rather
# than marginal.
#
# The polygon overlay is drawn client-side from 0-1 page fractions stored on
# doc_elements, so there is no server-side geometry and no dynamic origin.

variable "name_prefix" {
  type = string
}

variable "derived_bucket_id" {
  type = string
}

variable "derived_bucket_arn" {
  type = string
}

variable "derived_bucket_regional_domain_name" {
  type = string
}

variable "price_class" {
  description = "PriceClass_100 is North America and Europe. CBC is one office in Ohio."
  type        = string
  default     = "PriceClass_100"
}

# Origin Access Control, not the legacy Origin Access Identity: OAC signs with
# SigV4 and works with SSE-KMS, which OAI does not.
resource "aws_cloudfront_origin_access_control" "derived" {
  name                              = "${var.name_prefix}-derived"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "derived" {
  enabled = true
  comment = "${var.name_prefix} page rasters"

  origin {
    domain_name              = var.derived_bucket_regional_domain_name
    origin_id                = "derived"
    origin_access_control_id = aws_cloudfront_origin_access_control.derived.id
  }

  default_cache_behavior {
    target_origin_id       = "derived"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    # CachingOptimized. A raster is immutable once written — the key includes the
    # document version — so it can be cached indefinitely.
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  price_class = var.price_class
}

# Only this distribution may read the bucket. Combined with the public-access
# block in the storage module, the objects are unreachable any other way.
data "aws_iam_policy_document" "derived_bucket" {
  statement {
    sid       = "AllowCloudFrontServicePrincipal"
    actions   = ["s3:GetObject"]
    resources = ["${var.derived_bucket_arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.derived.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "derived" {
  bucket = var.derived_bucket_id
  policy = data.aws_iam_policy_document.derived_bucket.json
}

output "domain_name" {
  value = aws_cloudfront_distribution.derived.domain_name
}

output "distribution_id" {
  value = aws_cloudfront_distribution.derived.id
}
