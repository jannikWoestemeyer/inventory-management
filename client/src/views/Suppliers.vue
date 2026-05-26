<template>
  <div class="suppliers">
    <div class="page-header">
      <h2>{{ t('suppliers.title') }}</h2>
      <p>{{ t('suppliers.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else-if="!suppliers.length" class="no-data">
      {{ t('suppliers.noData') }}
    </div>
    <div v-else>
      <!-- Summary stats -->
      <div class="stats-grid" data-copilot-card="suppliers-summary">
        <div class="stat-card">
          <div class="stat-label">{{ t('suppliers.summary.totalSuppliers') }}</div>
          <div class="stat-value">{{ totalSuppliers.toLocaleString() }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('suppliers.summary.totalInventoryValue') }}</div>
          <div class="stat-value">{{ formatCurrency(totalInventoryValue, currentCurrency) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('suppliers.summary.avgItemsPerSupplier') }}</div>
          <div class="stat-value">{{ avgItems.toFixed(1) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('suppliers.summary.suppliersWithLowStock') }}</div>
          <div class="stat-value">{{ suppliersWithLowStock.toLocaleString() }}</div>
        </div>
      </div>

      <!-- Supplier breakdown table -->
      <div class="card" data-copilot-card="suppliers-table">
        <div class="card-header">
          <h3 class="card-title">{{ t('suppliers.table.title') }}</h3>
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('suppliers.table.supplier') }}</th>
                <th class="num">{{ t('suppliers.table.itemCount') }}</th>
                <th class="num">{{ t('suppliers.table.inventoryValue') }}</th>
                <th>{{ t('suppliers.table.categories') }}</th>
                <th class="num">{{ t('suppliers.table.avgLeadTime') }}</th>
                <th class="num">{{ t('suppliers.table.lowStock') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in suppliers" :key="s.name">
                <td><strong>{{ s.name }}</strong></td>
                <td class="num">{{ s.item_count.toLocaleString() }}</td>
                <td class="num">{{ formatCurrency(s.total_inventory_value, currentCurrency) }}</td>
                <td class="categories-cell">{{ (s.categories || []).join(', ') }}</td>
                <td class="num">{{ s.avg_lead_time_days.toFixed(1) }} {{ t('suppliers.days') }}</td>
                <td class="num">
                  <span :class="['badge', s.low_stock_count > 0 ? 'danger' : 'neutral']">
                    {{ s.low_stock_count.toLocaleString() }}
                  </span>
                </td>
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
  name: 'Suppliers',
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
    const suppliers = ref([])

    const loadData = async () => {
      loading.value = true
      error.value = null
      try {
        suppliers.value = await api.getSuppliers(getCurrentFilters())
      } catch (err) {
        error.value = 'Failed to load suppliers: ' + err.message
        console.error('Suppliers load error:', err)
      } finally {
        loading.value = false
      }
    }

    const totalSuppliers = computed(() => suppliers.value.length)

    const totalInventoryValue = computed(() =>
      suppliers.value.reduce((sum, s) => sum + (s.total_inventory_value || 0), 0)
    )

    const avgItems = computed(() => {
      if (suppliers.value.length === 0) return 0
      const totalItems = suppliers.value.reduce(
        (sum, s) => sum + (s.item_count || 0),
        0
      )
      return totalItems / suppliers.value.length
    })

    const suppliersWithLowStock = computed(
      () => suppliers.value.filter((s) => (s.low_stock_count || 0) > 0).length
    )

    // Watch all global filters; backend only honors warehouse + category,
    // but keeping all four consistent with the rest of the app is cheap.
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
      suppliers,
      totalSuppliers,
      totalInventoryValue,
      avgItems,
      suppliersWithLowStock,
      formatCurrency,
    }
  },
}
</script>

<style scoped>
.suppliers {
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
  margin-bottom: 1.5rem;
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

th {
  background: var(--paper-surface-warm);
  padding: 0.75rem;
  text-align: left;
  font-weight: 600;
  color: var(--ink-muted);
  border-bottom: 2px solid #e2e8f0;
  font-size: 0.875rem;
}

td {
  padding: 0.75rem;
  border-bottom: 1px solid #e2e8f0;
  color: var(--ink-strong);
  font-size: 0.9rem;
}

tr:hover {
  background: var(--paper-surface-warm);
}

.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

th.num {
  text-align: right;
}

.categories-cell {
  font-size: 0.8rem;
  color: var(--ink-body);
  max-width: 360px;
  white-space: normal;
  word-break: break-word;
  line-height: 1.4;
}

.badge {
  display: inline-block;
  padding: 0.2rem 0.65rem;
  border-radius: 9999px;
  font-size: 0.8rem;
  font-weight: 600;
  min-width: 2rem;
  text-align: center;
}

.badge.danger {
  background: rgba(181, 61, 52, 0.15);
  color: var(--danger);
}

.badge.neutral {
  background: var(--paper-surface-warm);
  color: var(--ink-body);
}

.loading {
  text-align: center;
  padding: 3rem;
  color: var(--ink-muted);
}

.no-data {
  padding: 3rem;
  text-align: center;
  color: var(--ink-muted);
  font-size: 0.95rem;
}

.error {
  background: rgba(181, 61, 52, 0.15);
  color: var(--danger);
  padding: 1rem;
  border-radius: 8px;
  margin: 1rem 0;
}
</style>
