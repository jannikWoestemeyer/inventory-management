<template>
  <div class="low-stock">
    <div class="page-header">
      <h2>{{ t('lowStock.title') }}</h2>
      <p>{{ t('lowStock.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>

    <!-- Friendly empty/healthy state: shown when zero items are below reorder under the
         current filter combination. Uses an inline check-mark SVG (no emoji per project rule). -->
    <div v-else-if="items.length === 0" class="card healthy-card">
      <svg
        class="healthy-icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="#10b981"
        stroke-width="2.5"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="10" />
        <path d="M8 12.5l2.5 2.5L16 9" />
      </svg>
      <h3 class="healthy-title">{{ t('lowStock.healthy.title') }}</h3>
      <p class="healthy-message">{{ t('lowStock.healthy.message') }}</p>
    </div>

    <div v-else>
      <!-- Summary stat cards -->
      <div class="stats-grid" data-copilot-card="low-stock-summary">
        <div class="stat-card">
          <div class="stat-label">{{ t('lowStock.summary.itemsBelow') }}</div>
          <div class="stat-value">{{ items.length.toLocaleString() }}</div>
        </div>
        <div class="stat-card danger">
          <div class="stat-label">{{ t('lowStock.summary.criticalCount') }}</div>
          <div class="stat-value">{{ criticalItems.length.toLocaleString() }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('lowStock.summary.warningCount') }}</div>
          <div class="stat-value">{{ warningItems.length.toLocaleString() }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('lowStock.summary.atRiskValue') }}</div>
          <div class="stat-value">{{ formatCurrency(atRiskValue, currentCurrency) }}</div>
        </div>
      </div>

      <!-- Items table -->
      <div class="card" data-copilot-card="low-stock-table">
        <div class="card-header">
          <h3 class="card-title">{{ t('lowStock.table.title') }}</h3>
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('lowStock.table.sku') }}</th>
                <th>{{ t('lowStock.table.item') }}</th>
                <th>{{ t('lowStock.table.category') }}</th>
                <th>{{ t('lowStock.table.warehouse') }}</th>
                <th>{{ t('lowStock.table.supplier') }}</th>
                <th class="num">{{ t('lowStock.table.onHand') }}</th>
                <th class="num">{{ t('lowStock.table.reorderPoint') }}</th>
                <th class="num">{{ t('lowStock.table.gap') }}</th>
                <th>{{ t('lowStock.table.severity') }}</th>
                <th class="num">{{ t('lowStock.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="item.id" :data-copilot-sku="item.sku">
                <td><code>{{ item.sku }}</code></td>
                <td>{{ item.name }}</td>
                <td>{{ item.category }}</td>
                <td>{{ item.warehouse }}</td>
                <td>{{ item.supplier_name }}</td>
                <td class="num">{{ item.quantity_on_hand.toLocaleString() }}</td>
                <td class="num">{{ item.reorder_point.toLocaleString() }}</td>
                <td class="num">{{ Math.max(0, item.reorder_point - item.quantity_on_hand).toLocaleString() }}</td>
                <td>
                  <span :class="['badge', severityFor(item) === 'critical' ? 'danger' : 'warning']">
                    {{ severityFor(item) === 'critical'
                      ? t('lowStock.severityCritical')
                      : t('lowStock.severityWarning') }}
                  </span>
                </td>
                <td class="num">{{ item.lead_time_days }} {{ t('lowStock.days') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted, watch } from 'vue'
import { api } from '../api'
import { useFilters } from '../composables/useFilters'
import { useI18n } from '../composables/useI18n'
import { formatCurrency } from '../utils/currency'

export default {
  name: 'LowStock',
  setup() {
    const { t, currentCurrency } = useI18n()
    const {
      selectedPeriod,
      selectedLocation,
      selectedCategory,
      selectedStatus,
      getCurrentFilters,
    } = useFilters()

    const loading = ref(true)
    const error = ref(null)
    const items = ref([])

    const loadData = async () => {
      loading.value = true
      error.value = null
      try {
        items.value = await api.getLowStockItems(getCurrentFilters())
      } catch (err) {
        error.value = 'Failed to load low-stock items: ' + err.message
        console.error('LowStock load error:', err)
      } finally {
        loading.value = false
      }
    }

    // Severity rule: ratio = quantity_on_hand / reorder_point
    //   ratio <= 0.5  → critical
    //   0.5 < ratio <= 1.0 → warning
    // If reorder_point is 0 (defensive), treat as critical to avoid divide-by-zero.
    const severityFor = (item) => {
      if (!item.reorder_point || item.reorder_point <= 0) return 'critical'
      const ratio = item.quantity_on_hand / item.reorder_point
      return ratio <= 0.5 ? 'critical' : 'warning'
    }

    const criticalItems = computed(() =>
      items.value.filter((i) => severityFor(i) === 'critical')
    )

    const warningItems = computed(() =>
      items.value.filter((i) => severityFor(i) === 'warning')
    )

    // At-risk value = sum of (gap * unit_cost) — the dollar value of inventory we're short.
    const atRiskValue = computed(() =>
      items.value.reduce((sum, i) => {
        const gap = Math.max(0, (i.reorder_point || 0) - (i.quantity_on_hand || 0))
        return sum + gap * (i.unit_cost || 0)
      }, 0)
    )

    watch(
      [selectedPeriod, selectedLocation, selectedCategory, selectedStatus],
      () => loadData()
    )

    onMounted(loadData)

    return {
      t,
      currentCurrency,
      loading,
      error,
      items,
      criticalItems,
      warningItems,
      atRiskValue,
      severityFor,
      formatCurrency,
    }
  },
}
</script>

<style scoped>
.low-stock {
  padding: 0;
}

.card {
  background: var(--paper-surface);
  border-radius: 12px;
  padding: 1.5rem;
  margin-bottom: 1.5rem;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}

.card-header {
  margin-bottom: 1.25rem;
}

.card-title {
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--ink-strong);
  margin: 0;
}

.table-container {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
}

thead th {
  background: var(--paper-surface-warm);
  padding: 0.75rem;
  text-align: left;
  font-weight: 600;
  color: var(--ink-muted);
  border-bottom: 2px solid #e2e8f0;
  font-size: 0.85rem;
}

tbody td {
  padding: 0.75rem;
  border-bottom: 1px solid #e2e8f0;
  font-size: 0.9rem;
}

tbody tr:hover {
  background: var(--paper-surface-warm);
}

.num { text-align: right; font-variant-numeric: tabular-nums; }
th.num { text-align: right; }

td code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.8rem;
  background: var(--paper-surface-warm);
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
}

/* Healthy / empty state */
.healthy-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 3rem 1.5rem;
}

.healthy-icon {
  width: 64px;
  height: 64px;
  margin-bottom: 1rem;
}

.healthy-title {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--success);
  margin: 0 0 0.5rem 0;
}

.healthy-message {
  color: var(--ink-muted);
  margin: 0;
  font-size: 0.95rem;
  max-width: 480px;
}

.loading {
  text-align: center;
  padding: 3rem;
  color: var(--ink-muted);
}

.error {
  background: rgba(181, 61, 52, 0.15);
  color: var(--danger);
  padding: 1rem;
  border-radius: 8px;
  margin: 1rem 0;
}
</style>
