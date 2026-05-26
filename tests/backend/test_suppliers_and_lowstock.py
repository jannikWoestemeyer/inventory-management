"""
Tests for the suppliers, low-stock inventory endpoint, and new inventory fields
(supplier_name + lead_time_days).
"""
import pytest


class TestSuppliersAndLowStock:
    """Test suite for /api/suppliers, /api/inventory/low-stock, and the
    supplier-related fields on /api/inventory."""

    # ------------------------------------------------------------------
    # 1) /api/suppliers
    # ------------------------------------------------------------------

    def test_get_all_suppliers(self, client):
        """GET /api/suppliers returns a non-empty list with the expected fields."""
        response = client.get("/api/suppliers")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        first = data[0]
        assert "name" in first
        assert "item_count" in first
        assert "total_inventory_value" in first
        assert "categories" in first
        assert "avg_lead_time_days" in first
        assert "low_stock_count" in first

    def test_supplier_field_types(self, client):
        """Supplier fields should have the right types."""
        response = client.get("/api/suppliers")
        data = response.json()
        assert len(data) > 0

        for supplier in data:
            assert isinstance(supplier["name"], str)
            assert isinstance(supplier["item_count"], int)
            assert isinstance(supplier["total_inventory_value"], (int, float))
            assert isinstance(supplier["categories"], list)
            for cat in supplier["categories"]:
                assert isinstance(cat, str)
            assert isinstance(supplier["avg_lead_time_days"], (int, float))
            assert isinstance(supplier["low_stock_count"], int)

            # Sanity ranges
            assert supplier["item_count"] >= 0
            assert supplier["total_inventory_value"] >= 0
            assert supplier["avg_lead_time_days"] >= 0
            assert supplier["low_stock_count"] >= 0
            assert supplier["low_stock_count"] <= supplier["item_count"]

    def test_suppliers_sorted_by_total_inventory_value_desc(self, client):
        """Suppliers are sorted by total_inventory_value descending."""
        response = client.get("/api/suppliers")
        data = response.json()
        assert len(data) > 1, "Need at least 2 suppliers to check ordering"

        values = [s["total_inventory_value"] for s in data]
        assert values == sorted(values, reverse=True), (
            "Suppliers should be sorted by total_inventory_value desc"
        )

    def test_filter_suppliers_by_warehouse(self, client):
        """Filtering by warehouse narrows the supplier list (or at most keeps it the same)."""
        unfiltered = client.get("/api/suppliers").json()
        filtered = client.get("/api/suppliers?warehouse=San Francisco").json()

        assert isinstance(filtered, list)
        # The filtered set must be a subset (by name) of the unfiltered set
        unfiltered_names = {s["name"] for s in unfiltered}
        filtered_names = {s["name"] for s in filtered}
        assert filtered_names.issubset(unfiltered_names)

        # Item counts for any shared supplier should be <= their unfiltered count
        unfiltered_by_name = {s["name"]: s for s in unfiltered}
        for s in filtered:
            assert s["item_count"] <= unfiltered_by_name[s["name"]]["item_count"]

    def test_filter_suppliers_by_category(self, client):
        """Filtering by category should yield suppliers whose categories include it."""
        response = client.get("/api/suppliers?category=Sensors")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)

        for supplier in data:
            # Categories were lowercased on the filter side; raw category strings are preserved.
            lowered = [c.lower() for c in supplier["categories"]]
            assert "sensors" in lowered, (
                f"Supplier {supplier['name']} should supply Sensors when filtered by category=Sensors"
            )

    # ------------------------------------------------------------------
    # 2) /api/inventory/low-stock (route ordering matters!)
    # ------------------------------------------------------------------

    def test_low_stock_endpoint_returns_list_not_404(self, client):
        """The /api/inventory/low-stock route must NOT be shadowed by /api/inventory/{item_id}."""
        response = client.get("/api/inventory/low-stock")
        # If the catch-all hit first, we'd get a 404 from get_inventory_item.
        assert response.status_code == 200, (
            f"Expected 200 from low-stock route, got {response.status_code}. "
            "This likely means /api/inventory/{item_id} is registered before low-stock."
        )

        data = response.json()
        assert isinstance(data, list)

    def test_low_stock_items_below_reorder_point(self, client):
        """Every item returned has quantity_on_hand <= reorder_point."""
        response = client.get("/api/inventory/low-stock")
        assert response.status_code == 200
        data = response.json()

        for item in data:
            assert "quantity_on_hand" in item
            assert "reorder_point" in item
            assert item["quantity_on_hand"] <= item["reorder_point"], (
                f"Item {item.get('sku')} should be low-stock but qty={item['quantity_on_hand']} "
                f"> reorder_point={item['reorder_point']}"
            )

    def test_low_stock_filter_by_warehouse(self, client):
        """Warehouse filter narrows low-stock items to that warehouse."""
        response = client.get("/api/inventory/low-stock?warehouse=San Francisco")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)

        for item in data:
            assert item["warehouse"] == "San Francisco"
            assert item["quantity_on_hand"] <= item["reorder_point"]

    def test_low_stock_subset_of_full_inventory(self, client):
        """Low-stock items should be a subset (by sku) of all inventory items."""
        all_items = client.get("/api/inventory").json()
        low_stock = client.get("/api/inventory/low-stock").json()

        all_skus = {item["sku"] for item in all_items}
        low_skus = {item["sku"] for item in low_stock}

        assert low_skus.issubset(all_skus)

    # ------------------------------------------------------------------
    # 3) New inventory fields: supplier_name + lead_time_days
    # ------------------------------------------------------------------

    def test_inventory_includes_supplier_and_lead_time(self, client):
        """Every inventory item exposes supplier_name (str) and lead_time_days (int)."""
        response = client.get("/api/inventory")
        assert response.status_code == 200

        data = response.json()
        assert len(data) > 0

        for item in data:
            assert "supplier_name" in item, f"Item {item.get('sku')} missing supplier_name"
            assert "lead_time_days" in item, f"Item {item.get('sku')} missing lead_time_days"

            # Allow Optional[None] for safety, but the populated data should be the right type.
            if item["supplier_name"] is not None:
                assert isinstance(item["supplier_name"], str)
                assert item["supplier_name"].strip() != ""
            if item["lead_time_days"] is not None:
                assert isinstance(item["lead_time_days"], int)
                assert item["lead_time_days"] >= 0

    def test_inventory_supplier_fields_populated_for_data(self, client):
        """At least some inventory items have non-null supplier_name and lead_time_days."""
        response = client.get("/api/inventory")
        data = response.json()

        with_supplier = [i for i in data if i.get("supplier_name")]
        with_lead = [i for i in data if i.get("lead_time_days") is not None]

        assert len(with_supplier) > 0, "Expected at least one item to have a supplier_name"
        assert len(with_lead) > 0, "Expected at least one item to have a lead_time_days"
