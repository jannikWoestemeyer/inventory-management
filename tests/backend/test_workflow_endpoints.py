"""
Tests for workflow-style endpoints: filtered reports, restocking orders, and tasks.
"""
import pytest


class TestWorkflowEndpoints:
    """Test suite for /api/reports/*, /api/restocking/orders, and /api/tasks."""

    # ------------------------------------------------------------------
    # /api/reports/quarterly
    # ------------------------------------------------------------------

    def test_quarterly_report_unfiltered(self, client):
        """Quarterly report returns a list of quarter aggregates."""
        response = client.get("/api/reports/quarterly")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        first = data[0]
        assert "quarter" in first
        assert "total_orders" in first
        assert "total_revenue" in first
        assert "delivered_orders" in first
        assert "avg_order_value" in first

    def test_quarterly_report_filter_changes_aggregates(self, client):
        """Filtering by warehouse should change (and not exceed) unfiltered totals."""
        unfiltered = client.get("/api/reports/quarterly").json()
        filtered = client.get("/api/reports/quarterly?warehouse=Tokyo").json()

        unfiltered_orders = sum(q["total_orders"] for q in unfiltered)
        filtered_orders = sum(q["total_orders"] for q in filtered)

        # Filtering should narrow (or at worst preserve) the set
        assert filtered_orders <= unfiltered_orders
        # And, given there are non-Tokyo orders, it should strictly reduce the total
        assert filtered_orders < unfiltered_orders, (
            "Expected Tokyo-only quarterly totals to be smaller than unfiltered totals"
        )

    def test_quarterly_report_status_filter(self, client):
        """status=Delivered means delivered_orders == total_orders per quarter."""
        response = client.get("/api/reports/quarterly?status=Delivered")
        assert response.status_code == 200

        data = response.json()
        for quarter in data:
            assert quarter["delivered_orders"] == quarter["total_orders"], (
                f"Quarter {quarter['quarter']} has total_orders={quarter['total_orders']} "
                f"but delivered_orders={quarter['delivered_orders']} when filtered to Delivered"
            )

    # ------------------------------------------------------------------
    # /api/reports/monthly-trends
    # ------------------------------------------------------------------

    def test_monthly_trends_unfiltered(self, client):
        """Monthly trends returns a list of month aggregates sorted by month."""
        response = client.get("/api/reports/monthly-trends")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        # Sorted by month asc
        months = [m["month"] for m in data]
        assert months == sorted(months), "Months should be in ascending order"

        first = data[0]
        assert "month" in first
        assert "order_count" in first
        assert "revenue" in first
        assert "delivered_count" in first

    def test_monthly_trends_filter_changes_aggregates(self, client):
        """Warehouse filter should narrow monthly aggregates."""
        unfiltered = client.get("/api/reports/monthly-trends").json()
        filtered = client.get("/api/reports/monthly-trends?warehouse=Tokyo").json()

        u_total = sum(m["order_count"] for m in unfiltered)
        f_total = sum(m["order_count"] for m in filtered)

        assert f_total <= u_total
        assert f_total < u_total, (
            "Expected Tokyo-only monthly totals to be smaller than unfiltered totals"
        )

    # ------------------------------------------------------------------
    # /api/restocking/orders
    # ------------------------------------------------------------------

    def test_restocking_orders_list_initially(self, client):
        """GET /api/restocking/orders returns a list."""
        response = client.get("/api/restocking/orders")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_create_restocking_order_then_appears_in_list(self, client):
        """POST a valid restocking order, then GET should include it."""
        before = client.get("/api/restocking/orders").json()
        before_count = len(before)

        payload = {
            "items": [
                {
                    "item_sku": "PCB-001",
                    "item_name": "Single Layer PCB Assembly",
                    "quantity": 100,
                    "unit_cost": 24.99,
                    "lead_time_days": 11,
                }
            ],
            "budget": 5000.0,
        }
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 201

        created = response.json()
        assert "id" in created
        assert "order_number" in created
        assert created["order_number"].startswith("RST-2025-")
        assert created["budget"] == 5000.0
        assert created["max_lead_time_days"] == 11
        # total_value should equal qty * unit_cost (within float tolerance)
        assert abs(created["total_value"] - (100 * 24.99)) < 0.01

        after = client.get("/api/restocking/orders").json()
        assert len(after) == before_count + 1
        assert any(o["id"] == created["id"] for o in after)

    def test_create_restocking_order_exceeding_budget_returns_400(self, client):
        """Total > budget should yield 400."""
        payload = {
            "items": [
                {
                    "item_sku": "PCB-001",
                    "item_name": "Single Layer PCB Assembly",
                    "quantity": 1000,
                    "unit_cost": 24.99,
                    "lead_time_days": 11,
                }
            ],
            "budget": 100.0,
        }
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 400
        body = response.json()
        assert "detail" in body
        assert "budget" in body["detail"].lower()

    def test_create_restocking_order_empty_items_returns_400(self, client):
        """Empty items list should yield 400."""
        payload = {"items": [], "budget": 1000.0}
        response = client.post("/api/restocking/orders", json=payload)
        assert response.status_code == 400
        body = response.json()
        assert "detail" in body
        assert "no items" in body["detail"].lower()

    # ------------------------------------------------------------------
    # /api/tasks
    # ------------------------------------------------------------------

    def test_tasks_list_returns_list(self, client):
        """GET /api/tasks returns a list (may be empty)."""
        response = client.get("/api/tasks")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_create_task_and_appears_in_list(self, client):
        """POST a task, then GET should include it with status=pending."""
        before_count = len(client.get("/api/tasks").json())

        payload = {"title": "Review low-stock alerts", "priority": "high"}
        response = client.post("/api/tasks", json=payload)
        assert response.status_code == 201

        created = response.json()
        assert created["title"] == "Review low-stock alerts"
        assert created["status"].lower() == "pending"
        assert created["priority"] == "high"
        assert "id" in created

        after = client.get("/api/tasks").json()
        assert len(after) == before_count + 1
        assert any(t["id"] == created["id"] for t in after)

    def test_patch_task_toggles_status(self, client):
        """PATCH should toggle status between pending and completed."""
        created = client.post(
            "/api/tasks", json={"title": "Toggle me"}
        ).json()
        task_id = created["id"]
        assert created["status"].lower() == "pending"

        # First toggle -> completed
        r1 = client.patch(f"/api/tasks/{task_id}")
        assert r1.status_code == 200
        assert r1.json()["status"].lower() == "completed"

        # Second toggle -> pending
        r2 = client.patch(f"/api/tasks/{task_id}")
        assert r2.status_code == 200
        assert r2.json()["status"].lower() == "pending"

    def test_delete_task_removes_it(self, client):
        """DELETE removes the task; subsequent PATCH returns 404."""
        created = client.post(
            "/api/tasks", json={"title": "Delete me"}
        ).json()
        task_id = created["id"]

        del_response = client.delete(f"/api/tasks/{task_id}")
        assert del_response.status_code == 200

        # PATCH the now-deleted task -> 404
        patch_response = client.patch(f"/api/tasks/{task_id}")
        assert patch_response.status_code == 404
        body = patch_response.json()
        assert "detail" in body
        assert "not found" in body["detail"].lower()

    def test_patch_nonexistent_task_returns_404(self, client):
        """PATCH on an id that doesn't exist returns 404."""
        response = client.patch("/api/tasks/nonexistent-task-id-9999")
        assert response.status_code == 404
        body = response.json()
        assert "detail" in body
        assert "not found" in body["detail"].lower()

    def test_delete_nonexistent_task_returns_404(self, client):
        """DELETE on an id that doesn't exist returns 404."""
        response = client.delete("/api/tasks/nonexistent-task-id-9999")
        assert response.status_code == 404
        body = response.json()
        assert "detail" in body
        assert "not found" in body["detail"].lower()
