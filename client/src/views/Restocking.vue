<template>
  <div class="restocking">
    <div class="page-header">
      <h2>Restocking</h2>
      <p>Set a budget. We'll recommend what to restock based on the demand forecast gap, then place the order.</p>
    </div>

    <div v-if="loading" class="loading">Loading demand forecast…</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">Budget</h3>
          <div class="budget-display">${{ budget.toLocaleString() }}</div>
        </div>
        <input
          type="range"
          v-model.number="budget"
          :min="0"
          :max="250000"
          :step="1000"
          class="budget-slider"
        />
        <div class="slider-bounds">
          <span>$0</span>
          <span>$250,000</span>
        </div>
      </div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">Items recommended</div>
          <div class="stat-value">{{ recommendations.length }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">Total cost</div>
          <div class="stat-value">${{ totalCost.toLocaleString(undefined, { maximumFractionDigits: 0 }) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Budget remaining</div>
          <div class="stat-value">${{ budgetRemaining.toLocaleString(undefined, { maximumFractionDigits: 0 }) }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">Max lead time</div>
          <div class="stat-value">{{ maxLeadTime }} <span style="font-size: 1rem; font-weight: 500;">days</span></div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">Recommended items ({{ recommendations.length }})</h3>
          <button
            class="primary-btn"
            :disabled="recommendations.length === 0 || submitting"
            @click="placeOrder"
          >
            {{ submitting ? 'Placing…' : 'Place Order' }}
          </button>
        </div>

        <div v-if="recommendations.length === 0" class="empty-state">
          Increase the budget to see restocking recommendations.
        </div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>SKU</th>
                <th>Item</th>
                <th>Trend</th>
                <th class="num">Gap (forecast − current)</th>
                <th class="num">Qty to restock</th>
                <th class="num">Unit cost</th>
                <th class="num">Line cost</th>
                <th class="num">Lead time</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="rec in recommendations" :key="rec.item_sku">
                <td><code>{{ rec.item_sku }}</code></td>
                <td>{{ rec.item_name }}</td>
                <td><span :class="['badge', rec.trend]">{{ rec.trend }}</span></td>
                <td class="num">{{ rec.gap }}</td>
                <td class="num">{{ rec.quantity }}</td>
                <td class="num">${{ rec.unit_cost.toFixed(2) }}</td>
                <td class="num"><strong>${{ rec.line_cost.toLocaleString(undefined, { maximumFractionDigits: 2 }) }}</strong></td>
                <td class="num">{{ rec.lead_time_days }} days</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="lastSubmitted" class="success-banner">
        Order <strong>{{ lastSubmitted.order_number }}</strong> submitted —
        ${{ lastSubmitted.total_value.toLocaleString() }} across {{ lastSubmitted.items.length }} item(s).
        Max delivery in {{ lastSubmitted.max_lead_time_days }} days.
        See the Orders tab for details.
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'

export default {
  name: 'Restocking',
  setup() {
    const loading = ref(true)
    const error = ref(null)
    const submitting = ref(false)
    const forecasts = ref([])
    const budget = ref(50000)
    const lastSubmitted = ref(null)

    const loadForecasts = async () => {
      try {
        loading.value = true
        forecasts.value = await api.getDemandForecasts()
      } catch (err) {
        error.value = 'Failed to load demand forecast: ' + err.message
      } finally {
        loading.value = false
      }
    }

    // Greedy gap-fill: rank by (forecasted - current) demand, biggest gap first;
    // fill the gap if the line cost fits in the remaining budget, else partial fit.
    const recommendations = computed(() => {
      const ranked = forecasts.value
        .map((f) => ({
          ...f,
          gap: Math.max(0, f.forecasted_demand - f.current_demand),
        }))
        .filter((f) => f.gap > 0 && f.unit_cost > 0)
        .sort((a, b) => b.gap - a.gap)

      const picks = []
      let remaining = budget.value
      for (const f of ranked) {
        if (remaining <= 0) break
        const desired = f.gap
        const fullLineCost = desired * f.unit_cost
        let qty = desired
        if (fullLineCost > remaining) {
          qty = Math.floor(remaining / f.unit_cost)
          if (qty <= 0) continue
        }
        const lineCost = qty * f.unit_cost
        picks.push({
          item_sku: f.item_sku,
          item_name: f.item_name,
          trend: f.trend,
          gap: desired,
          quantity: qty,
          unit_cost: f.unit_cost,
          line_cost: lineCost,
          lead_time_days: f.lead_time_days,
        })
        remaining -= lineCost
      }
      return picks
    })

    const totalCost = computed(() =>
      recommendations.value.reduce((sum, r) => sum + r.line_cost, 0)
    )
    const budgetRemaining = computed(() => Math.max(0, budget.value - totalCost.value))
    const maxLeadTime = computed(() =>
      recommendations.value.length === 0
        ? 0
        : Math.max(...recommendations.value.map((r) => r.lead_time_days))
    )

    const placeOrder = async () => {
      if (recommendations.value.length === 0) return
      submitting.value = true
      error.value = null
      try {
        const payload = {
          budget: budget.value,
          items: recommendations.value.map((r) => ({
            item_sku: r.item_sku,
            item_name: r.item_name,
            quantity: r.quantity,
            unit_cost: r.unit_cost,
            lead_time_days: r.lead_time_days,
          })),
        }
        lastSubmitted.value = await api.createRestockingOrder(payload)
      } catch (err) {
        error.value =
          'Failed to submit order: ' + (err.response?.data?.detail || err.message)
      } finally {
        submitting.value = false
      }
    }

    onMounted(loadForecasts)

    return {
      loading,
      error,
      submitting,
      budget,
      recommendations,
      totalCost,
      budgetRemaining,
      maxLeadTime,
      lastSubmitted,
      placeOrder,
    }
  },
}
</script>

<style scoped>
.budget-display {
  font-size: 1.75rem;
  font-weight: 700;
  color: #2563eb;
  letter-spacing: -0.025em;
}

.budget-slider {
  width: 100%;
  margin-top: 0.75rem;
  accent-color: #2563eb;
}

.slider-bounds {
  display: flex;
  justify-content: space-between;
  margin-top: 0.5rem;
  color: #64748b;
  font-size: 0.8rem;
}

.primary-btn {
  background: #2563eb;
  color: white;
  border: none;
  padding: 0.55rem 1.25rem;
  border-radius: 6px;
  font-weight: 600;
  font-size: 0.9rem;
  cursor: pointer;
  transition: background 0.15s ease;
}
.primary-btn:hover:not(:disabled) { background: #1d4ed8; }
.primary-btn:disabled { background: #cbd5e1; cursor: not-allowed; }

.num { text-align: right; font-variant-numeric: tabular-nums; }
th.num { text-align: right; }
td code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.8rem;
  background: #f1f5f9;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
}

.empty-state {
  padding: 2rem;
  text-align: center;
  color: #64748b;
  font-size: 0.95rem;
}

.success-banner {
  margin-top: 1rem;
  background: #d1fae5;
  border: 1px solid #6ee7b7;
  color: #065f46;
  padding: 0.875rem 1.125rem;
  border-radius: 8px;
  font-size: 0.9rem;
}
</style>
