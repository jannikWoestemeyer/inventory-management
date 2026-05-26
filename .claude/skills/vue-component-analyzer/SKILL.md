---
name: vue-component-analyzer
description: Analyzes Vue 3 single-file components for performance and code-reuse issues and recommends concrete refactors. Use this skill when the user asks for a Vue performance review, component refactoring, reusability analysis, or audit of one or more .vue files in the client/ directory.
---

# Vue Component Analyzer

Static-analysis playbook for Vue 3 Composition API single-file components in this repo. Produces a prioritized list of findings with file:line citations and concrete remediations. Aligns with `client/CLAUDE.md` conventions (Composition API, refs vs computed, scoped styles, unique v-for keys, debounced watches, async components).

## When to invoke

Invoke this skill when the user asks to:
- Review or audit one or more `.vue` files for performance.
- Suggest refactors for reuse (extracting composables, splitting large components).
- Look for reactivity bugs, wasted re-renders, or prop misuse.
- Identify which components in `client/src/` should be split or share logic.

Do NOT invoke for: backend changes, pure styling tweaks, or new-feature scaffolding that does not touch existing components.

## Inputs

- A specific `.vue` file path, OR
- A directory (default: `client/src/views/` and `client/src/components/`), OR
- The full client when no path is given.

Always start by listing the target files and reading each one fully. Cross-reference `client/src/composables/` to see what reuse infrastructure already exists before recommending new composables.

## Analysis checklist

Walk every component through these passes. Record each hit with `file:line` and a severity tag (`critical`, `warning`, `nit`).

### 1. Reactivity correctness (critical)

- `v-for` using `index` as `:key` instead of a stable id (`item.id`, `sku`, `month`).
- Direct prop mutation in `setup()` (`props.foo = ...`, `props.list.push(...)`).
- Destructured props in `setup(props)` that break reactivity (`const { foo } = props`).
- Missing `.value` on refs inside `<script>` (or stray `.value` in `<template>`).
- `new Date(x).getMonth()` / `.getTime()` etc. without an `isNaN` guard.

### 2. Performance — computed vs methods (warning)

- Methods called from the template that perform filtering, mapping, reducing, sorting, or `toLocaleString` formatting on each render. These belong in `computed()`.
- Computed properties that perform side effects (API calls, mutations) — move to `watch` or method.
- Computed properties that depend on non-reactive values (module-level `let`, `Date.now()`) — they will not invalidate.
- Heavy synchronous work in `onMounted` without a loading state.

### 3. Performance — render & DOM cost (warning)

- `v-if` on a node that toggles often (tab switching, chart visibility) — prefer `v-show`.
- `v-show` on a heavy subtree that is rarely shown — prefer `v-if` or `defineAsyncComponent`.
- Large static imports of chart/table libraries that are not rendered on first paint — wrap in `defineAsyncComponent(() => import('...'))`.
- `watch` on a search/filter ref that triggers an API call without debounce. Use `watchDebounced` from `@vueuse/core` (already the repo convention).
- Inline object/array literals passed as props (`:filters="{ a, b }"`) that change identity each render and bust child memoization.

### 4. Code reuse (warning / nit)

- Two or more components holding the same `selectedCategory` / `selectedWarehouse` / `selectedMonth` refs and `loadX()` patterns — extract into a composable in `client/src/composables/` (see existing `useFilters` pattern in `client/CLAUDE.md`).
- Duplicated `loading` / `error` / `try/catch/finally` data-loading scaffolding — extract `useAsyncData(fetcher)` composable.
- Inline currency / large-number / percent formatting copy-pasted across files — extract `composables/useFormat.js` or `utils/format.js`.
- Components over ~150 lines of `<script>` or ~100 lines of `<template>` — recommend splitting (per `client/CLAUDE.md` "When to extract component").
- Parent components reaching into child state via `ref` instead of `props down / events up`.

### 5. Repo-specific anti-patterns (critical)

These come up often in this codebase — flag aggressively:

- `:key="index"` in `v-for` over orders / inventory / chart data. Use `order.id`, `item.sku`, `bucket.month`.
- `new Date(order.order_date).getMonth()` without `isNaN(date.getTime())` check — known to crash on missing/invalid dates.
- Mutating a prop array (e.g. `props.orders.sort(...)`) instead of cloning into a computed.
- Mixing Options API (`data() {}`, `methods: {}`) with Composition API in the same file.
- Chart components recomputing SVG path strings in a method instead of a computed property.
- Filter UI components that import the API client directly instead of emitting changes upward to the view.

## Output format

Return a single markdown report with this structure. No emojis.

```
# Vue Component Analysis

## Summary
- Files reviewed: N
- Critical: X | Warnings: Y | Nits: Z
- Top three recommendations: ...

## Findings

### [critical] client/src/views/OrdersView.vue:142 — v-for uses index as key
Current:
  <tr v-for="(order, idx) in orders" :key="idx">
Why it matters: Vue reuses DOM rows incorrectly when the list re-sorts after a filter change, causing stale row data and detached event listeners.
Fix:
  <tr v-for="order in orders" :key="order.id">

### [warning] client/src/views/DashboardView.vue:88 — formatCurrency called from template on every render
...

## Recommended refactors

1. Extract `useFilters()` from OrdersView + InventoryView + DashboardView into client/src/composables/useFilters.js.
   Files affected: ...
   Effort: ~30 min.

2. Split SpendingView.vue (412 lines) into SpendingView + SpendingTrendChart + SpendingCategoryBreakdown.
   ...
```

Order findings by severity, then by file. Always cite `path:line`. Always show a minimal before/after when proposing a code change.

## Remediation snippets

Use these as the canonical fixes. They match `client/CLAUDE.md`.

**Stable v-for key**
```vue
<tr v-for="order in orders" :key="order.id">
```

**Method to computed**
```javascript
// before: called from template on each render
const totalValue = () => orders.value.reduce((s, o) => s + o.total_value, 0)

// after: cached until orders changes
const totalValue = computed(() =>
  orders.value.reduce((s, o) => s + o.total_value, 0)
)
```

**Safe date parsing**
```javascript
const d = new Date(order.order_date)
if (Number.isNaN(d.getTime())) return null
return d.getMonth()
```

**Debounced search watch**
```javascript
import { watchDebounced } from '@vueuse/core'
watchDebounced(searchQuery, () => loadData(), { debounce: 300 })
```

**Lazy-load a heavy chart**
```javascript
import { defineAsyncComponent } from 'vue'
const SpendingTrendChart = defineAsyncComponent(() =>
  import('@/components/SpendingTrendChart.vue')
)
```

**Don't mutate props — emit up**
```javascript
// child
emit('update:filters', { ...props.filters, category })

// parent
<FilterBar :filters="filters" @update:filters="filters = $event" />
```

**Extract a composable**
```javascript
// client/src/composables/useAsyncData.js
import { ref } from 'vue'
export function useAsyncData(fetcher) {
  const data = ref(null)
  const loading = ref(false)
  const error = ref(null)
  const run = async (...args) => {
    try {
      loading.value = true
      error.value = null
      data.value = await fetcher(...args)
    } catch (e) {
      error.value = e?.message ?? 'Failed to load'
    } finally {
      loading.value = false
    }
  }
  return { data, loading, error, run }
}
```

## Operating rules

- Do not modify any `.vue` files during analysis. Produce the report only; let the user (or `vue-expert` subagent) apply fixes.
- If the user asks to apply fixes, delegate to the `vue-expert` subagent per the repo's mandatory rule for `.vue` edits.
- Prefer extracting to `client/src/composables/` over adding mixins or global state.
- Never recommend introducing Vuex/Pinia unless the user explicitly asks — the repo uses composables for shared state.
- Keep findings actionable. Skip stylistic nits the user did not ask about.
