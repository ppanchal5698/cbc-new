"""
The central reference library (FR-3, §7.5).

    Maintain a central structured reference library of hardware sets and standard
    line items, **independent of any single job file**.

Explicitly not per-project: this is the fix for the Excel-workbook-per-job status
quo, where hardware sets lived inside whichever job file last used them.
"""


import pytest
from django.db import IntegrityError, transaction
from factories import CatalogItemFactory, FinishCodeFactory
from rest_framework import status

from catalog.models import CatalogItem

pytestmark = pytest.mark.django_db


class TestLibraryIsIndependentOfJobs:
    def test_catalog_item_has_no_project_foreign_key(self):
        """The whole point of FR-3: the library outlives any one bid."""
        assert not any(f.name == "project" for f in CatalogItem._meta.get_fields())

    def test_items_survive_project_deletion(self):
        from factories import ProjectFactory

        item = CatalogItemFactory()
        ProjectFactory().delete()
        assert CatalogItem.objects.filter(id=item.id).exists()


class TestHardConstraintFields:
    def test_fire_rating_is_nullable_because_unrated_is_a_real_state(self):
        """Rated hardware is a distinct certified product line, not a spec note."""
        assert CatalogItemFactory(fire_rating_minutes=None).fire_rating_minutes is None
        assert CatalogItemFactory(sku="RATED-1", fire_rating_minutes=90).fire_rating_minutes == 90

    def test_handing_is_nullable_because_not_everything_is_handed(self):
        """Handed parts are separate SKUs; unhanded items have no hand."""
        assert CatalogItemFactory(sku="UNHANDED").handing is None
        assert CatalogItemFactory(sku="LH-1", handing="LH").handing == "LH"

    def test_csi_division_separates_openings_from_accessories(self):
        """A Division 10 accessory never matches a Division 08 opening (§6.1)."""
        door = CatalogItemFactory(sku="D-1", csi_division="08")
        accessory = CatalogItemFactory(sku="A-1", csi_division="10")
        assert door.csi_division != accessory.csi_division


class TestP21Nullability:
    def test_p21_item_id_is_nullable_on_purpose(self):
        """
        Risk R3.

        P21 item IDs diverge from manufacturer part numbers and semi-custom items
        will not match cleanly, so a null here is the normal case rather than
        missing data to be filled in by a similarity guess.
        """
        assert CatalogItemFactory(p21_item_id=None).p21_item_id is None


class TestStockFlag:
    def test_is_stock_marks_the_top_n_list(self):
        """
        NR-13: automate the stock and top-N items; beyond that a clear MANUAL
        cut-off. NR-6 (CBC's actual list) blocks Phase 3 go-live.
        """
        assert CatalogItemFactory(sku="S-1", is_stock=True).is_stock is True
        assert CatalogItemFactory(sku="NS-1", is_stock=False).is_stock is False


class TestCatalogApi:
    def test_vendor_and_sku_are_unique_together(self):
        CatalogItemFactory(vendor="Hager", sku="BB1279")
        with pytest.raises(IntegrityError), transaction.atomic():
            CatalogItem.objects.create(vendor="Hager", sku="BB1279", description="dup")

    def test_finish_code_is_exposed_in_both_nomenclatures(self, auth_client):
        finish = FinishCodeFactory(us_code="US26D", bhma_code="626")
        item = CatalogItemFactory(finish_code=finish)
        body = auth_client.get(f"/api/catalog-items/{item.id}/").data
        assert body["finish_us_code"] == "US26D"
        assert body["finish_bhma_code"] == "626"

    def test_filter_by_stock_and_division(self, auth_client):
        CatalogItemFactory(sku="A", is_stock=True, csi_division="08")
        CatalogItemFactory(sku="B", is_stock=False, csi_division="08")
        CatalogItemFactory(sku="C", is_stock=True, csi_division="10")
        assert auth_client.get("/api/catalog-items/?is_stock=true&csi_division=08").data["count"] == 1

    def test_search_covers_part_numbers(self, auth_client):
        CatalogItemFactory(sku="X-1", part_number="ND53PD")
        assert auth_client.get("/api/catalog-items/?search=ND53PD").data["count"] == 1

    def test_library_is_writable_by_an_estimator(self, auth_client):
        """FR-3 requires maintaining the library, not just reading it."""
        response = auth_client.post(
            "/api/catalog-items/",
            {
                "vendor": "Bobrick", "sku": "B-6806", "description": "Grab bar 36in",
                "list_price": "64.00", "product_type_band": "RESTROOM_PARTITIONS",
                "csi_division": "10", "is_stock": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
