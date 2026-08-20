"""
factory_boy factories for every model.

Import from tests via ``from factories import ProjectFactory, ...`` — ``api/`` is
on the pytest path.

Defaults are deliberately *realistic*: a 3070 LH opening with a 90-minute rating
and a US26D finish is the shape of a real CBC line, so a test that forgets to set
something still exercises a plausible case rather than an empty one.
"""

import uuid
from datetime import date
from decimal import Decimal

import factory
from catalog.models import CatalogItem
from django.contrib.auth import get_user_model
from feedback.models import Feedback
from openings.models import (
    DocElement,
    ExtractionRun,
    FieldProvenance,
    FieldProvenanceElement,
    Match,
    Opening,
)
from pricing.models import FinishCode, MarginBand, TaxRate, ThroatDepth, VendorMultiplier
from projects.models import Document, DocumentManifest, PipelineJob, Project
from quotes.models import Quote, QuoteLine, VendorRFQ


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()
        django_get_or_create = ("username",)
        # set_password already saves; the extra post-generation save is redundant.
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"estimator{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@cbc.test")
    password = factory.PostGenerationMethodCall("set_password", "testpass123")


class ProjectFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Project

    name = factory.Sequence(lambda n: f"Project {n}")
    source_channel = "MANUAL"
    initiator_email = factory.Sequence(lambda n: f"initiator{n}@cbc.test")
    brand = "McDonald's"
    architect = "Architects Inc."
    general_contractor = "GC Corp"


class DocumentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Document

    project = factory.SubFactory(ProjectFactory)
    filename = factory.Sequence(lambda n: f"bid_set_{n}.pdf")
    file_key = factory.LazyAttribute(
        lambda o: f"projects/{o.project.id}/source/{uuid.uuid4()}/v1/original.pdf"
    )
    file_version_id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    checksum_sha256 = factory.LazyFunction(lambda: uuid.uuid4().hex * 2)
    status = "UPLOADED"
    role = "BID_SET"
    page_count = 65
    size_bytes = 24_960_068


class DocumentManifestFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DocumentManifest

    document = factory.SubFactory(DocumentFactory)
    page_number = factory.Sequence(lambda n: n + 1)
    text_layer = "RICH"
    native_word_count = 1200
    vector_path_count = 40
    page_class = "DOOR_SCHEDULE"
    class_method = "KEYWORD"
    class_confidence = Decimal("0.8000")
    ocr_route = "TEXTRACT_TABLES"
    route_reason = "cell/row/column structure is the whole point"
    ocr_cost_estimate = Decimal("0.015000")
    width_pt = 3024.0
    height_pt = 2160.0
    rotation = 0


class PipelineJobFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = PipelineJob

    document = factory.SubFactory(DocumentFactory)
    project = factory.LazyAttribute(lambda o: o.document.project)
    stage = "PREPROCESS"
    status = "PENDING"
    idempotency_key = factory.LazyFunction(lambda: uuid.uuid4().hex)


class DocElementFactory(factory.django.DjangoModelFactory):
    """Positional element_path, matching what normalisation actually writes (§7.2)."""

    class Meta:
        model = DocElement

    document = factory.SubFactory(DocumentFactory)
    element_path = factory.Sequence(lambda n: f"pages/1/words/{n}")
    page_number = 1
    element_type = "word"
    text = factory.Sequence(lambda n: f"word{n}")
    ocr_confidence = 0.97
    reading_order = factory.Sequence(lambda n: n)
    x0 = 0.10
    y0 = 0.20
    x1 = 0.30
    y1 = 0.20
    x2 = 0.30
    y2 = 0.25
    x3 = 0.10
    y3 = 0.25
    bbox_x_min = 0.10
    bbox_y_min = 0.20
    bbox_x_max = 0.30
    bbox_y_max = 0.25


class ExtractionRunFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ExtractionRun

    document = factory.SubFactory(DocumentFactory)
    # A resolved inference-profile ID, not a bare foundation-model string (C5).
    model_id = "us.anthropic.claude-test-v1:0"
    model_id_cheap = "us.anthropic.claude-test-haiku-v1:0"
    prompt_version = "v1"
    inference_params = factory.LazyFunction(
        lambda: {"temperature": 0.0, "top_p": 1.0, "max_tokens": 8192}
    )
    status = "COMPLETED"


class FinishCodeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FinishCode
        django_get_or_create = ("us_code",)

    us_code = "US26D"
    bhma_code = "626"
    description = "Satin chrome on brass"
    base_metal = "brass"


class ThroatDepthFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ThroatDepth
        django_get_or_create = ("wall_type",)

    wall_type = "Masonry"
    throat_depth_inches = Decimal("5.750")


class OpeningFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Opening

    project = factory.SubFactory(ProjectFactory)
    extraction_run = factory.SubFactory(ExtractionRunFactory)
    door_number = factory.Sequence(lambda n: f"D-{100 + n}")
    size_raw = "3070"
    width_inches = 36
    height_inches = 84
    handing = "LH"
    handing_absent = False
    finish_raw = "US26D"
    fire_rating_raw = "90 MIN"
    fire_rating_minutes = 90
    fire_rating_absent = False
    fire_rating_source_location = "DOOR_SCHEDULE"
    hardware_group = "HW-3"
    review_state = "AUTO"


class FieldProvenanceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FieldProvenance

    extraction_run = factory.SubFactory(ExtractionRunFactory)
    opening = factory.SubFactory(OpeningFactory)
    field_name = "fire_rating"
    extracted_value = "90 MIN"
    ocr_confidence = 0.95
    llm_confidence = 0.88
    completeness_penalty = 1.0
    final_confidence = 0.88
    grounding_score = 100.0
    page_number = 1
    review_state = "AUTO"


class FieldProvenanceElementFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FieldProvenanceElement

    field_provenance = factory.SubFactory(FieldProvenanceFactory)
    doc_element = factory.SubFactory(DocElementFactory)
    ordinal = 0


class CatalogItemFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = CatalogItem
        django_get_or_create = ("vendor", "sku")

    vendor = "Hager"
    sku = factory.Sequence(lambda n: f"BB1279-{n:03d}")
    series = "1279"
    description = "Full mortise butt hinge"
    list_price = Decimal("38.00")
    product_type_band = "COMMODITY"
    line_group = "DOOR"
    csi_division = "08"
    fire_rating_minutes = None
    handing = None
    is_stock = True


class MarginBandFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = MarginBand
        django_get_or_create = ("product_type_band", "effective_date")

    product_type_band = "COMMODITY"
    target_margin_pct = Decimal("0.2700")
    floor_margin_pct = Decimal("0.2700")
    effective_date = date(2024, 1, 1)


class VendorMultiplierFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VendorMultiplier
        django_get_or_create = ("vendor_name", "tier", "effective_date")

    vendor_name = "Hager"
    tier = "Standard"
    multiplier = Decimal("0.2900")
    source_sheet_version = "TEST-SHEET"
    effective_date = date(2024, 1, 1)


class TaxRateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TaxRate
        django_get_or_create = ("jurisdiction", "effective_date")

    #: Two-letter state code. Only OH and KY are taxable (§1.1).
    jurisdiction = "OH"
    rate_pct = Decimal("0.0800")
    effective_date = date(2024, 1, 1)


class MatchFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Match

    opening = factory.SubFactory(OpeningFactory)
    catalog_item = factory.SubFactory(CatalogItemFactory)
    rank = 1
    match_confidence = 0.92
    rating_ok = True
    handing_ok = True
    division_ok = True
    finish_ok = True
    status = "PROPOSED"


class QuoteFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Quote

    project = factory.SubFactory(ProjectFactory)
    created_by = factory.SubFactory(UserFactory)
    status = "DRAFT"
    tax_jurisdiction = "OH"


class QuoteLineFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = QuoteLine

    quote = factory.SubFactory(QuoteFactory)
    catalog_item = factory.SubFactory(CatalogItemFactory)
    line_group = "DOOR"
    description = "Full mortise butt hinge"
    quantity = Decimal("1.00")
    our_cost = Decimal("11.0200")
    cost_source = "MFR_LIST"
    margin_pct = Decimal("0.2700")
    line_order = factory.Sequence(lambda n: n + 1)


class VendorRFQFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = VendorRFQ

    quote_line = factory.SubFactory(QuoteLineFactory)
    vendor = "Hager"
    status = "REQUESTED"


class FeedbackFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Feedback

    entity_type = "FIELD_PROVENANCE"
    entity_id = factory.LazyFunction(uuid.uuid4)
    field_name = "fire_rating"
    value_before = "90 MIN"
    value_after = "45 MIN"
    changed_by = factory.SubFactory(UserFactory)
