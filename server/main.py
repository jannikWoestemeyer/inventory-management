from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel
from mock_data import inventory_items, orders, demand_forecasts, backlog_items, spending_summary, monthly_spending, category_spending, recent_transactions, purchase_orders

app = FastAPI(title="Factory Inventory Management System")

# Quarter mapping for date filtering
QUARTER_MAP = {
    'Q1-2025': ['2025-01', '2025-02', '2025-03'],
    'Q2-2025': ['2025-04', '2025-05', '2025-06'],
    'Q3-2025': ['2025-07', '2025-08', '2025-09'],
    'Q4-2025': ['2025-10', '2025-11', '2025-12']
}

def filter_by_month(items: list, month: Optional[str]) -> list:
    """Filter items by month/quarter based on order_date field"""
    if not month or month == 'all':
        return items

    if month.startswith('Q'):
        # Handle quarters
        if month in QUARTER_MAP:
            months = QUARTER_MAP[month]
            return [item for item in items if any(m in item.get('order_date', '') for m in months)]
    else:
        # Direct month match
        return [item for item in items if month in item.get('order_date', '')]

    return items

def apply_filters(items: list, warehouse: Optional[str] = None, category: Optional[str] = None,
                 status: Optional[str] = None) -> list:
    """Apply common filters to a list of items"""
    filtered = items

    if warehouse and warehouse != 'all':
        filtered = [item for item in filtered if item.get('warehouse') == warehouse]

    if category and category != 'all':
        filtered = [item for item in filtered if item.get('category', '').lower() == category.lower()]

    if status and status != 'all':
        filtered = [item for item in filtered if item.get('status', '').lower() == status.lower()]

    return filtered

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class InventoryItem(BaseModel):
    id: str
    sku: str
    name: str
    category: str
    warehouse: str
    quantity_on_hand: int
    reorder_point: int
    unit_cost: float
    location: str
    last_updated: str
    supplier_name: Optional[str] = None
    lead_time_days: Optional[int] = None


class Supplier(BaseModel):
    name: str
    item_count: int
    total_inventory_value: float
    categories: List[str]
    avg_lead_time_days: float
    low_stock_count: int

class Order(BaseModel):
    id: str
    order_number: str
    customer: str
    items: List[dict]
    status: str
    order_date: str
    expected_delivery: str
    total_value: float
    actual_delivery: Optional[str] = None
    warehouse: Optional[str] = None
    category: Optional[str] = None

class DemandForecast(BaseModel):
    id: str
    item_sku: str
    item_name: str
    current_demand: int
    forecasted_demand: int
    trend: str
    period: str
    unit_cost: Optional[float] = None
    lead_time_days: Optional[int] = None


class RestockingOrderItem(BaseModel):
    item_sku: str
    item_name: str
    quantity: int
    unit_cost: float
    lead_time_days: int


class RestockingOrder(BaseModel):
    id: str
    order_number: str
    items: List[RestockingOrderItem]
    total_value: float
    budget: float
    submitted_at: str
    max_lead_time_days: int


class CreateRestockingOrderRequest(BaseModel):
    items: List[RestockingOrderItem]
    budget: float


class Task(BaseModel):
    id: str
    title: str
    status: str = "pending"  # "pending" or "completed"
    priority: Optional[str] = None
    due_date: Optional[str] = None
    created_at: Optional[str] = None


class CreateTaskRequest(BaseModel):
    title: str
    priority: Optional[str] = None
    due_date: Optional[str] = None

class BacklogItem(BaseModel):
    id: str
    order_id: str
    item_sku: str
    item_name: str
    quantity_needed: int
    quantity_available: int
    days_delayed: int
    priority: str
    has_purchase_order: Optional[bool] = False

class PurchaseOrder(BaseModel):
    id: str
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    status: str
    created_date: str
    notes: Optional[str] = None

class CreatePurchaseOrderRequest(BaseModel):
    backlog_item_id: str
    supplier_name: str
    quantity: int
    unit_cost: float
    expected_delivery_date: str
    notes: Optional[str] = None

# API endpoints
@app.get("/")
def root():
    return {"message": "Factory Inventory Management System API", "version": "1.0.0"}

@app.get("/api/inventory", response_model=List[InventoryItem])
def get_inventory(
    warehouse: Optional[str] = None,
    category: Optional[str] = None
):
    """Get all inventory items with optional filtering"""
    return apply_filters(inventory_items, warehouse, category)

@app.get("/api/inventory/low-stock", response_model=List[InventoryItem])
def get_low_stock_items(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
):
    """Return inventory items at or below their reorder point (filter-aware)."""
    filtered = apply_filters(inventory_items, warehouse, category)
    return [item for item in filtered if item["quantity_on_hand"] <= item["reorder_point"]]


@app.get("/api/inventory/{item_id}", response_model=InventoryItem)
def get_inventory_item(item_id: str):
    """Get a specific inventory item"""
    item = next((item for item in inventory_items if item["id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.get("/api/suppliers", response_model=List[Supplier])
def get_suppliers(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
):
    """Aggregate suppliers across the (filtered) inventory: item count, value,
    categories supplied, average lead time, and how many items are at or below
    reorder point."""
    filtered = apply_filters(inventory_items, warehouse, category)

    by_supplier: dict = {}
    for item in filtered:
        name = item.get("supplier_name") or "Unknown"
        bucket = by_supplier.setdefault(
            name,
            {
                "name": name,
                "item_count": 0,
                "total_inventory_value": 0.0,
                "categories": set(),
                "lead_time_sum": 0,
                "lead_time_n": 0,
                "low_stock_count": 0,
            },
        )
        bucket["item_count"] += 1
        bucket["total_inventory_value"] += item["quantity_on_hand"] * item["unit_cost"]
        bucket["categories"].add(item["category"])
        if item.get("lead_time_days") is not None:
            bucket["lead_time_sum"] += item["lead_time_days"]
            bucket["lead_time_n"] += 1
        if item["quantity_on_hand"] <= item["reorder_point"]:
            bucket["low_stock_count"] += 1

    out: List[dict] = []
    for s in by_supplier.values():
        out.append(
            {
                "name": s["name"],
                "item_count": s["item_count"],
                "total_inventory_value": round(s["total_inventory_value"], 2),
                "categories": sorted(s["categories"]),
                "avg_lead_time_days": (
                    round(s["lead_time_sum"] / s["lead_time_n"], 1)
                    if s["lead_time_n"]
                    else 0.0
                ),
                "low_stock_count": s["low_stock_count"],
            }
        )
    out.sort(key=lambda x: x["total_inventory_value"], reverse=True)
    return out

@app.get("/api/orders", response_model=List[Order])
def get_orders(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get all orders with optional filtering"""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)
    return filtered_orders

@app.get("/api/orders/{order_id}", response_model=Order)
def get_order(order_id: str):
    """Get a specific order"""
    order = next((order for order in orders if order["id"] == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

@app.get("/api/demand", response_model=List[DemandForecast])
def get_demand_forecasts():
    """Get demand forecasts"""
    return demand_forecasts

@app.get("/api/backlog", response_model=List[BacklogItem])
def get_backlog():
    """Get backlog items with purchase order status"""
    # Add has_purchase_order flag to each backlog item
    result = []
    for item in backlog_items:
        item_dict = dict(item)
        # Check if this backlog item has a purchase order
        has_po = any(po["backlog_item_id"] == item["id"] for po in purchase_orders)
        item_dict["has_purchase_order"] = has_po
        result.append(item_dict)
    return result

@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None
):
    """Get summary statistics for dashboard with optional filtering"""
    # Filter inventory
    filtered_inventory = apply_filters(inventory_items, warehouse, category)

    # Filter orders
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    total_inventory_value = sum(item["quantity_on_hand"] * item["unit_cost"] for item in filtered_inventory)
    low_stock_items = len([item for item in filtered_inventory if item["quantity_on_hand"] <= item["reorder_point"]])
    pending_orders = len([order for order in filtered_orders if order["status"] in ["Processing", "Backordered"]])
    total_backlog_items = len(backlog_items)

    return {
        "total_inventory_value": round(total_inventory_value, 2),
        "low_stock_items": low_stock_items,
        "pending_orders": pending_orders,
        "total_backlog_items": total_backlog_items,
        "total_orders_value": sum(order["total_value"] for order in filtered_orders)
    }

@app.get("/api/spending/summary")
def get_spending_summary():
    """Get spending summary statistics"""
    return spending_summary

@app.get("/api/spending/monthly")
def get_monthly_spending():
    """Get monthly spending breakdown"""
    return monthly_spending

@app.get("/api/spending/categories")
def get_category_spending():
    """Get spending by category"""
    return category_spending

@app.get("/api/spending/transactions")
def get_recent_transactions():
    """Get recent transactions"""
    return recent_transactions

@app.get("/api/reports/quarterly")
def get_quarterly_reports(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None,
):
    """Get quarterly performance reports honoring the global filters."""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    quarters = {}

    for order in filtered_orders:
        order_date = order.get('order_date', '')
        # Determine quarter
        if '2025-01' in order_date or '2025-02' in order_date or '2025-03' in order_date:
            quarter = 'Q1-2025'
        elif '2025-04' in order_date or '2025-05' in order_date or '2025-06' in order_date:
            quarter = 'Q2-2025'
        elif '2025-07' in order_date or '2025-08' in order_date or '2025-09' in order_date:
            quarter = 'Q3-2025'
        elif '2025-10' in order_date or '2025-11' in order_date or '2025-12' in order_date:
            quarter = 'Q4-2025'
        else:
            continue

        if quarter not in quarters:
            quarters[quarter] = {
                'quarter': quarter,
                'total_orders': 0,
                'total_revenue': 0,
                'delivered_orders': 0,
                'avg_order_value': 0
            }

        quarters[quarter]['total_orders'] += 1
        quarters[quarter]['total_revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            quarters[quarter]['delivered_orders'] += 1

    # Calculate averages and fulfillment rate
    result = []
    for q, data in quarters.items():
        if data['total_orders'] > 0:
            data['avg_order_value'] = round(data['total_revenue'] / data['total_orders'], 2)
            data['fulfillment_rate'] = round((data['delivered_orders'] / data['total_orders']) * 100, 1)
        result.append(data)

    # Sort by quarter
    result.sort(key=lambda x: x['quarter'])
    return result

@app.get("/api/reports/monthly-trends")
def get_monthly_trends(
    warehouse: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    month: Optional[str] = None,
):
    """Get month-over-month trends honoring the global filters."""
    filtered_orders = apply_filters(orders, warehouse, category, status)
    filtered_orders = filter_by_month(filtered_orders, month)

    months = {}

    for order in filtered_orders:
        order_date = order.get('order_date', '')
        if not order_date:
            continue

        # Extract month (format: YYYY-MM-DD)
        month = order_date[:7]  # Gets YYYY-MM

        if month not in months:
            months[month] = {
                'month': month,
                'order_count': 0,
                'revenue': 0,
                'delivered_count': 0
            }

        months[month]['order_count'] += 1
        months[month]['revenue'] += order.get('total_value', 0)
        if order.get('status') == 'Delivered':
            months[month]['delivered_count'] += 1

    # Convert to list and sort
    result = list(months.values())
    result.sort(key=lambda x: x['month'])
    return result

# In-memory store for restocking orders (resets on server restart)
submitted_restocking_orders: List[dict] = []

# In-memory store for user tasks (resets on server restart)
user_tasks: List[dict] = []


@app.get("/api/tasks", response_model=List[Task])
def list_tasks():
    """Return all user tasks (in-memory, resets on server restart)."""
    return user_tasks


@app.post("/api/tasks", response_model=Task, status_code=201)
def create_task(payload: CreateTaskRequest):
    """Create a new task."""
    from datetime import datetime, timezone

    next_id = str(len(user_tasks) + 1)
    task = {
        "id": next_id,
        "title": payload.title,
        "status": "pending",
        "priority": payload.priority,
        "due_date": payload.due_date,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    user_tasks.append(task)
    return task


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str):
    """Delete a task by id."""
    for i, t in enumerate(user_tasks):
        if t["id"] == task_id:
            user_tasks.pop(i)
            return {"deleted": task_id}
    raise HTTPException(status_code=404, detail=f"Task {task_id} not found")


@app.patch("/api/tasks/{task_id}", response_model=Task)
def toggle_task(task_id: str):
    """Toggle a task's status between 'pending' and 'completed'."""
    for t in user_tasks:
        if t["id"] == task_id:
            t["status"] = "completed" if t["status"] == "pending" else "pending"
            return t
    raise HTTPException(status_code=404, detail=f"Task {task_id} not found")


@app.get("/api/restocking/orders", response_model=List[RestockingOrder])
def get_restocking_orders():
    """Get all submitted restocking orders."""
    return submitted_restocking_orders


@app.post("/api/restocking/orders", response_model=RestockingOrder, status_code=201)
def create_restocking_order(payload: CreateRestockingOrderRequest):
    """Submit a new restocking order built from demand-forecast recommendations."""
    if not payload.items:
        raise HTTPException(status_code=400, detail="No items in restocking order")

    from datetime import datetime, timezone

    total_value = sum(item.quantity * item.unit_cost for item in payload.items)
    if total_value > payload.budget + 0.01:
        raise HTTPException(
            status_code=400,
            detail=f"Order total ${total_value:.2f} exceeds budget ${payload.budget:.2f}",
        )

    max_lead = max(item.lead_time_days for item in payload.items)
    next_id = str(len(submitted_restocking_orders) + 1)
    order_number = f"RST-2025-{int(next_id):04d}"

    order = {
        "id": next_id,
        "order_number": order_number,
        "items": [item.model_dump() for item in payload.items],
        "total_value": round(total_value, 2),
        "budget": payload.budget,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "max_lead_time_days": max_lead,
    }
    submitted_restocking_orders.append(order)
    return order


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
