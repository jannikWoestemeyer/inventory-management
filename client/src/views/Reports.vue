<template>
  <div class="reports">
    <div class="page-header">
      <h2>{{ t('reports.title') }}</h2>
      <p>{{ t('reports.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else-if="!quarterlyData.length && !monthlyData.length" class="no-data">
      {{ t('reports.noData') }}
    </div>
    <div v-else>
      <!-- Quarterly Performance -->
      <div class="card" v-if="quarterlyData.length > 0" data-copilot-card="reports-quarterly">
        <div class="card-header">
          <h3 class="card-title">{{ t('reports.quarterly.title') }}</h3>
        </div>
        <div class="table-container">
          <table class="reports-table">
            <thead>
              <tr>
                <th>{{ t('reports.quarterly.quarter') }}</th>
                <th>{{ t('reports.quarterly.totalOrders') }}</th>
                <th>{{ t('reports.quarterly.totalRevenue') }}</th>
                <th>{{ t('reports.quarterly.avgOrderValue') }}</th>
                <th>{{ t('reports.quarterly.fulfillmentRate') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="q in quarterlyData" :key="q.quarter">
                <td><strong>{{ q.quarter }}</strong></td>
                <td>{{ q.total_orders }}</td>
                <td>{{ formatCurrency(q.total_revenue, currentCurrency) }}</td>
                <td>{{ formatCurrency(q.avg_order_value, currentCurrency) }}</td>
                <td>
                  <span :class="getFulfillmentClass(q.fulfillment_rate)">
                    {{ q.fulfillment_rate }}%
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Monthly Trends Chart -->
      <div class="card" v-if="monthlyData.length > 0" data-copilot-card="reports-trend">
        <div class="card-header">
          <h3 class="card-title">{{ t('reports.monthly.title') }}</h3>
        </div>
        <div class="chart-container">
          <div class="bar-chart">
            <div v-for="m in monthlyData" :key="m.month" class="bar-wrapper">
              <div class="bar-container">
                <div
                  class="bar"
                  :style="{ height: getBarHeight(m.revenue) + 'px' }"
                  :title="formatCurrency(m.revenue, currentCurrency)"
                ></div>
              </div>
              <div class="bar-label">{{ formatMonth(m.month) }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Month-over-Month Comparison -->
      <div class="card" v-if="monthlyData.length > 0">
        <div class="card-header">
          <h3 class="card-title">{{ t('reports.monthly.analysisTitle') }}</h3>
        </div>
        <div class="table-container">
          <table class="reports-table">
            <thead>
              <tr>
                <th>{{ t('reports.monthly.month') }}</th>
                <th>{{ t('reports.monthly.orders') }}</th>
                <th>{{ t('reports.monthly.revenue') }}</th>
                <th>{{ t('reports.monthly.change') }}</th>
                <th>{{ t('reports.monthly.growthRate') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(m, index) in monthlyData" :key="m.month">
                <td><strong>{{ formatMonth(m.month) }}</strong></td>
                <td>{{ m.order_count }}</td>
                <td>{{ formatCurrency(m.revenue, currentCurrency) }}</td>
                <td>
                  <span v-if="index > 0" :class="getChangeClass(m.revenue, monthlyData[index - 1].revenue)">
                    {{ getChangeValue(m.revenue, monthlyData[index - 1].revenue) }}
                  </span>
                  <span v-else>—</span>
                </td>
                <td>
                  <span v-if="index > 0" :class="getChangeClass(m.revenue, monthlyData[index - 1].revenue)">
                    {{ getGrowthRate(m.revenue, monthlyData[index - 1].revenue) }}
                  </span>
                  <span v-else>—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Summary Stats -->
      <div class="stats-grid" v-if="monthlyData.length > 0">
        <div class="stat-card">
          <div class="stat-label">{{ t('reports.summary.totalRevenue') }}</div>
          <div class="stat-value">{{ formatCurrency(totalRevenue, currentCurrency) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('reports.summary.avgMonthlyRevenue') }}</div>
          <div class="stat-value">{{ formatCurrency(avgMonthlyRevenue, currentCurrency) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('reports.summary.totalOrders') }}</div>
          <div class="stat-value">{{ totalOrders.toLocaleString() }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('reports.summary.bestQuarter') }}</div>
          <div class="stat-value">{{ bestQuarter || '—' }}</div>
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
  name: 'Reports',
  setup() {
    const { t, currentCurrency, currentLocale } = useI18n()
    const {
      selectedPeriod,
      selectedLocation,
      selectedCategory,
      selectedStatus,
      getCurrentFilters,
    } = useFilters()

    const loading = ref(true)
    const error = ref(null)
    const quarterlyData = ref([])
    const monthlyData = ref([])

    const loadData = async () => {
      loading.value = true
      error.value = null
      try {
        const filters = getCurrentFilters()
        const [quarterly, monthly] = await Promise.all([
          api.getQuarterlyReports(filters),
          api.getMonthlyTrends(filters),
        ])
        quarterlyData.value = quarterly
        monthlyData.value = monthly
      } catch (err) {
        error.value = 'Failed to load reports: ' + err.message
        console.error('Reports load error:', err)
      } finally {
        loading.value = false
      }
    }

    // Cache maxRevenue once per data change so getBarHeight is O(1) per call.
    const maxRevenue = computed(() => {
      if (monthlyData.value.length === 0) return 0
      return Math.max(...monthlyData.value.map((m) => m.revenue))
    })

    const totalRevenue = computed(() =>
      monthlyData.value.reduce((sum, m) => sum + m.revenue, 0)
    )

    const avgMonthlyRevenue = computed(() => {
      if (monthlyData.value.length === 0) return 0
      return totalRevenue.value / monthlyData.value.length
    })

    const totalOrders = computed(() =>
      monthlyData.value.reduce((sum, m) => sum + m.order_count, 0)
    )

    const bestQuarter = computed(() => {
      if (quarterlyData.value.length === 0) return ''
      return quarterlyData.value.reduce((best, q) =>
        q.total_revenue > best.total_revenue ? q : best
      ).quarter
    })

    const getBarHeight = (revenue) => {
      if (!maxRevenue.value) return 0
      return (revenue / maxRevenue.value) * 200
    }

    const getFulfillmentClass = (rate) => {
      if (rate >= 90) return 'badge success'
      if (rate >= 75) return 'badge warning'
      return 'badge danger'
    }

    const getChangeValue = (current, previous) => {
      const change = current - previous
      if (change === 0) return formatCurrency(0, currentCurrency.value)
      const sign = change > 0 ? '+' : '-'
      return sign + formatCurrency(Math.abs(change), currentCurrency.value)
    }

    const getChangeClass = (current, previous) => {
      const change = current - previous
      if (change > 0) return 'positive-change'
      if (change < 0) return 'negative-change'
      return ''
    }

    const getGrowthRate = (current, previous) => {
      if (previous === 0) return t('reports.growthNotAvailable')
      const rate = ((current - previous) / previous) * 100
      const sign = rate > 0 ? '+' : ''
      return sign + rate.toFixed(1) + '%'
    }

    // Parse 'YYYY-MM' safely; fall back to raw string if malformed.
    const formatMonth = (monthStr) => {
      if (!monthStr || typeof monthStr !== 'string') return ''
      const [year, month] = monthStr.split('-')
      const monthIndex = parseInt(month, 10) - 1
      if (Number.isNaN(monthIndex) || monthIndex < 0 || monthIndex > 11) {
        return monthStr
      }
      const date = new Date(Number(year), monthIndex, 1)
      if (Number.isNaN(date.getTime())) return monthStr
      const locale = currentLocale.value === 'ja' ? 'ja-JP' : 'en-US'
      return date.toLocaleDateString(locale, { month: 'short', year: 'numeric' })
    }

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
      quarterlyData,
      monthlyData,
      totalRevenue,
      avgMonthlyRevenue,
      totalOrders,
      bestQuarter,
      getBarHeight,
      getFulfillmentClass,
      getChangeValue,
      getChangeClass,
      getGrowthRate,
      formatMonth,
      formatCurrency,
    }
  },
}
</script>

<style scoped>
.reports {
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

.reports-table {
  width: 100%;
  border-collapse: collapse;
}

.reports-table th {
  background: var(--paper-surface-warm);
  padding: 0.75rem;
  text-align: left;
  font-weight: 600;
  color: var(--ink-muted);
  border-bottom: 2px solid var(--pencil);
}

.reports-table td {
  padding: 0.75rem;
  border-bottom: 1px solid var(--pencil-soft);
}

.reports-table tr:hover {
  background: var(--paper-surface-warm);
}

.chart-container {
  padding: 2rem 1rem;
  min-height: 300px;
}

.bar-chart {
  display: flex;
  align-items: flex-end;
  justify-content: space-around;
  height: 250px;
  gap: 0.5rem;
}

.bar-wrapper {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
  max-width: 80px;
}

.bar-container {
  height: 200px;
  display: flex;
  align-items: flex-end;
  width: 100%;
}

.bar {
  width: 100%;
  background: linear-gradient(to top, #3b82f6, #60a5fa);
  border-radius: 4px 4px 0 0;
  transition: all 0.3s;
  cursor: pointer;
}

.bar:hover {
  background: linear-gradient(to top, #2563eb, #3b82f6);
}

.bar-label {
  margin-top: 1.5rem;
  font-size: 0.75rem;
  color: var(--ink-muted);
  text-align: center;
  transform: rotate(-45deg);
  white-space: nowrap;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1rem;
  margin-top: 1.5rem;
}

.stat-card {
  background: var(--paper-surface);
  border-radius: 12px;
  padding: 1.5rem;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  border-left: 4px solid var(--ink-blue);
}

.stat-label {
  font-size: 0.875rem;
  color: var(--ink-muted);
  margin-bottom: 0.5rem;
}

.stat-value {
  font-size: 1.875rem;
  font-weight: 700;
  color: var(--ink-strong);
}

.badge {
  padding: 0.25rem 0.75rem;
  border-radius: 9999px;
  font-size: 0.875rem;
  font-weight: 500;
}

.badge.success { background: rgba(60, 138, 76, 0.18); color: var(--success); }
.badge.warning { background: rgba(201, 123, 31, 0.18); color: var(--warning); }
.badge.danger  { background: rgba(181, 61, 52, 0.15); color: var(--danger); }

.positive-change { color: var(--success); font-weight: 600; }
.negative-change { color: var(--danger); font-weight: 600; }

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
