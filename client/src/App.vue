<template>
  <div class="app">
    <header class="top-nav">
      <div class="nav-container">
        <div class="logo">
          <h1>{{ t('nav.companyName') }}</h1>
          <span class="subtitle">{{ t('nav.subtitle') }}</span>
        </div>
        <nav class="nav-tabs">
          <router-link to="/" :class="{ active: $route.path === '/' }">
            {{ t('nav.overview') }}
          </router-link>
          <router-link to="/inventory" :class="{ active: $route.path === '/inventory' }">
            {{ t('nav.inventory') }}
          </router-link>
          <router-link to="/orders" :class="{ active: $route.path === '/orders' }">
            {{ t('nav.orders') }}
          </router-link>
          <router-link to="/spending" :class="{ active: $route.path === '/spending' }">
            {{ t('nav.finance') }}
          </router-link>
          <router-link to="/demand" :class="{ active: $route.path === '/demand' }">
            {{ t('nav.demandForecast') }}
          </router-link>
          <router-link to="/reports" :class="{ active: $route.path === '/reports' }">
            {{ t('nav.reports') }}
          </router-link>
          <router-link to="/restocking" :class="{ active: $route.path === '/restocking' }">
            {{ t('nav.restocking') }}
          </router-link>
          <router-link to="/backlog" :class="{ active: $route.path === '/backlog' }">
            {{ t('nav.backlog') }}
          </router-link>
          <router-link to="/suppliers" :class="{ active: $route.path === '/suppliers' }">
            {{ t('nav.suppliers') }}
          </router-link>
          <router-link to="/low-stock" :class="{ active: $route.path === '/low-stock' }">
            {{ t('nav.lowStock') }}
          </router-link>
        </nav>
        <button
          class="theme-toggle"
          :aria-label="theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'"
          :title="theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'"
          @click="toggleTheme"
        >
          {{ theme === 'dark' ? 'Light' : 'Dark' }}
        </button>
        <LanguageSwitcher />
        <ProfileMenu
          @show-profile-details="showProfileDetails = true"
          @show-tasks="showTasks = true"
        />
      </div>
    </header>
    <FilterBar />
    <main class="main-content">
      <router-view />
    </main>

    <ProfileDetailsModal
      :is-open="showProfileDetails"
      @close="showProfileDetails = false"
    />

    <TasksModal
      :is-open="showTasks"
      :tasks="tasks"
      @close="showTasks = false"
      @add-task="addTask"
      @delete-task="deleteTask"
      @toggle-task="toggleTask"
    />
  </div>
</template>

<script>
import { ref, onMounted, computed, watch } from 'vue'
import { api } from './api'
import { useAuth } from './composables/useAuth'
import { useI18n } from './composables/useI18n'
import FilterBar from './components/FilterBar.vue'
import ProfileMenu from './components/ProfileMenu.vue'
import ProfileDetailsModal from './components/ProfileDetailsModal.vue'
import TasksModal from './components/TasksModal.vue'
import LanguageSwitcher from './components/LanguageSwitcher.vue'

const THEME_STORAGE_KEY = 'inventory-app-theme'

export default {
  name: 'App',
  components: {
    FilterBar,
    ProfileMenu,
    ProfileDetailsModal,
    TasksModal,
    LanguageSwitcher
  },
  setup() {
    const { currentUser } = useAuth()
    const { t } = useI18n()
    const showProfileDetails = ref(false)
    const showTasks = ref(false)
    const apiTasks = ref([])

    // Theme: prefer stored choice, else honor prefers-color-scheme, else light
    const initialTheme = () => {
      const stored = localStorage.getItem(THEME_STORAGE_KEY)
      if (stored === 'dark' || stored === 'light') return stored
      if (window.matchMedia?.('(prefers-color-scheme: dark)').matches) return 'dark'
      return 'light'
    }
    const theme = ref(initialTheme())
    const applyTheme = (value) => {
      document.documentElement.setAttribute('data-theme', value)
    }
    applyTheme(theme.value)
    watch(theme, (next) => {
      applyTheme(next)
      localStorage.setItem(THEME_STORAGE_KEY, next)
    })
    const toggleTheme = () => {
      theme.value = theme.value === 'dark' ? 'light' : 'dark'
    }

    // Merge mock tasks from currentUser with API tasks
    const tasks = computed(() => {
      return [...currentUser.value.tasks, ...apiTasks.value]
    })

    const loadTasks = async () => {
      try {
        apiTasks.value = await api.getTasks()
      } catch (err) {
        console.error('Failed to load tasks:', err)
      }
    }

    const addTask = async (taskData) => {
      try {
        const newTask = await api.createTask(taskData)
        // Add new task to the beginning of the array
        apiTasks.value.unshift(newTask)
      } catch (err) {
        console.error('Failed to add task:', err)
      }
    }

    const deleteTask = async (taskId) => {
      try {
        // Check if it's a mock task (from currentUser)
        const isMockTask = currentUser.value.tasks.some(t => t.id === taskId)

        if (isMockTask) {
          // Remove from mock tasks
          const index = currentUser.value.tasks.findIndex(t => t.id === taskId)
          if (index !== -1) {
            currentUser.value.tasks.splice(index, 1)
          }
        } else {
          // Remove from API tasks
          await api.deleteTask(taskId)
          apiTasks.value = apiTasks.value.filter(t => t.id !== taskId)
        }
      } catch (err) {
        console.error('Failed to delete task:', err)
      }
    }

    const toggleTask = async (taskId) => {
      try {
        // Check if it's a mock task (from currentUser)
        const mockTask = currentUser.value.tasks.find(t => t.id === taskId)

        if (mockTask) {
          // Toggle mock task status
          mockTask.status = mockTask.status === 'pending' ? 'completed' : 'pending'
        } else {
          // Toggle API task
          const updatedTask = await api.toggleTask(taskId)
          const index = apiTasks.value.findIndex(t => t.id === taskId)
          if (index !== -1) {
            apiTasks.value[index] = updatedTask
          }
        }
      } catch (err) {
        console.error('Failed to toggle task:', err)
      }
    }

    onMounted(loadTasks)

    return {
      t,
      theme,
      toggleTheme,
      showProfileDetails,
      showTasks,
      tasks,
      addTask,
      deleteTask,
      toggleTask
    }
  }
}
</script>

<style>
/* ============================================================
   Handwritten Sketchbook Theme
   - Caveat for display (headings, stat-values)
   - Patrick Hand for body, tables, nav, labels
   - Paper cream surfaces with pencil-tone borders
   ============================================================ */

:root {
  --paper-bg: #fdfaf3;
  --paper-surface: #fffdf7;
  --paper-surface-warm: #f5ead0;
  --pencil: #cdbfa3;
  --pencil-soft: #e3d8bf;
  --ink-strong: #2b2a26;
  --ink-body: #4a4842;
  --ink-muted: #8a8474;
  --ink-blue: #3b6fb3;
  --highlighter: #fcf3a6;
  --row-hover: #fbf1c7;
  --success: #3c8a4c;
  --warning: #c97b1f;
  --danger: #b53d34;
  --info: #3b6fb3;
  --font-hand: 'Patrick Hand', 'Comic Sans MS', 'Segoe UI', sans-serif;
  --font-display: 'Caveat', 'Patrick Hand', 'Comic Sans MS', cursive;
  --shadow-soft: 0 2px 6px rgba(43, 42, 38, 0.06);
  --shadow-lift: 0 6px 18px rgba(43, 42, 38, 0.08);
  --wavy-underline: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='80' height='4' viewBox='0 0 80 4'><path d='M0 2 C 20 0, 40 4, 60 2 C 70 1, 75 3, 80 2' stroke='%233b6fb3' stroke-width='1.5' fill='none'/></svg>");
}

/* ------------------ Handwritten Dark (charcoal moleskine) ------------------
   Re-skin the same tokens for dark mode: warm-black paper, cream ink,
   brighter sketch-blue, punchier status colors. The sketch underline gets
   a lighter ink so it still reads against the dark surface. */
[data-theme="dark"] {
  --paper-bg: #1c1a17;
  --paper-surface: #26221d;
  --paper-surface-warm: #2e2820;
  --pencil: #5a5346;
  --pencil-soft: #3d3830;
  --ink-strong: #f5ecd9;
  --ink-body: #d8cfb9;
  --ink-muted: #9b937e;
  --ink-blue: #7ab0e8;
  --highlighter: #5b4a18;
  --row-hover: #2d2620;
  --success: #5ec672;
  --warning: #e8a04a;
  --danger: #e85a4f;
  --info: #7ab0e8;
  --shadow-soft: 0 2px 6px rgba(0, 0, 0, 0.35);
  --shadow-lift: 0 6px 18px rgba(0, 0, 0, 0.45);
  --wavy-underline: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='80' height='4' viewBox='0 0 80 4'><path d='M0 2 C 20 0, 40 4, 60 2 C 70 1, 75 3, 80 2' stroke='%237ab0e8' stroke-width='1.5' fill='none'/></svg>");
  color-scheme: dark;
}

[data-theme="dark"] body {
  background-image:
    radial-gradient(circle at 25% 25%, rgba(245, 236, 217, 0.025) 0%, transparent 50%),
    radial-gradient(circle at 75% 75%, rgba(245, 236, 217, 0.025) 0%, transparent 50%),
    radial-gradient(circle at 50% 10%, rgba(245, 236, 217, 0.012) 0%, transparent 40%);
}

/* Dark-mode badge re-tints: cream-on-dark variants, keep semantic class names. */
[data-theme="dark"] .badge.success,
[data-theme="dark"] .badge.increasing {
  background: rgba(94, 198, 114, 0.18);
  color: #8ee8a0;
}
[data-theme="dark"] .badge.warning,
[data-theme="dark"] .badge.medium {
  background: rgba(232, 160, 74, 0.18);
  color: #f3c285;
}
[data-theme="dark"] .badge.danger,
[data-theme="dark"] .badge.decreasing,
[data-theme="dark"] .badge.high {
  background: rgba(232, 90, 79, 0.18);
  color: #f49890;
}
[data-theme="dark"] .badge.info,
[data-theme="dark"] .badge.stable,
[data-theme="dark"] .badge.low {
  background: rgba(122, 176, 232, 0.18);
  color: #a8cdf0;
}

/* The .error block is hand-tinted in light; soften the bright pink wash on dark */
[data-theme="dark"] .error {
  background: rgba(232, 90, 79, 0.12);
  border-color: rgba(232, 90, 79, 0.30);
  color: #f49890;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: var(--font-hand);
  font-size: 17px;
  line-height: 1.45;
  color: var(--ink-body);
  background-color: var(--paper-bg);
  background-image:
    radial-gradient(circle at 25% 25%, rgba(205, 191, 163, 0.06) 0%, transparent 50%),
    radial-gradient(circle at 75% 75%, rgba(205, 191, 163, 0.06) 0%, transparent 50%),
    radial-gradient(circle at 50% 10%, rgba(205, 191, 163, 0.03) 0%, transparent 40%);
  background-attachment: fixed;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.app {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

/* ------------------ Top Navigation (notebook spine) ------------------ */
.top-nav {
  background: var(--paper-surface);
  border-bottom: 2px solid var(--pencil);
  box-shadow: 0 2px 0 var(--pencil-soft), 0 4px 10px rgba(43, 42, 38, 0.04);
  position: sticky;
  top: 0;
  z-index: 100;
}

.nav-container {
  max-width: 1600px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  padding: 0 2rem;
  height: 76px;
}

.nav-container > .nav-tabs {
  margin-left: auto;
  margin-right: 1.5rem;
  flex: 1 1 auto;
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
  /* Fade-out the right edge so partially-clipped tabs look intentional and
     don't visually collide with the theme toggle. */
  mask-image: linear-gradient(to right, black calc(100% - 28px), transparent);
  -webkit-mask-image: linear-gradient(to right, black calc(100% - 28px), transparent);
  padding-right: 8px;
}
.nav-container > .nav-tabs::-webkit-scrollbar { display: none; }

.nav-container > .language-switcher {
  margin-right: 1rem;
}

.logo {
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
}

.logo h1 {
  font-family: var(--font-display);
  font-size: 2.1rem;
  font-weight: 700;
  color: var(--ink-strong);
  line-height: 1;
  letter-spacing: 0.01em;
}

.subtitle {
  font-family: var(--font-hand);
  font-size: 1rem;
  color: var(--ink-muted);
  font-weight: 400;
  padding-left: 0.75rem;
  border-left: 1px dashed var(--pencil);
}

.nav-tabs {
  display: flex;
  gap: 0.15rem;
  flex-wrap: nowrap;
  white-space: nowrap;
}

.nav-tabs a {
  flex-shrink: 0;
}

.nav-tabs a {
  font-family: var(--font-hand);
  padding: 0.45rem 0.9rem 0.55rem;
  color: var(--ink-body);
  text-decoration: none;
  font-size: 1.05rem;
  border-radius: 8px 10px 7px 9px;
  transition: color 0.15s ease, background-color 0.15s ease, transform 0.15s ease;
  position: relative;
}

.nav-tabs a:hover {
  color: var(--ink-strong);
  background: rgba(252, 243, 166, 0.45);
}

.nav-tabs a.active {
  color: var(--ink-strong);
  background: var(--highlighter);
}

.nav-tabs a.active::after {
  content: '';
  position: absolute;
  bottom: -6px;
  left: 4px;
  right: 4px;
  height: 5px;
  background: var(--wavy-underline) repeat-x;
  background-size: 80px 4px;
}

/* ------------------ Main Content ------------------ */
.main-content {
  flex: 1;
  max-width: 1600px;
  width: 100%;
  margin: 0 auto;
  padding: 1.75rem 2rem 3rem;
}

/* ------------------ Page Header ------------------ */
.page-header {
  margin-bottom: 1.75rem;
}

.page-header h2 {
  font-family: var(--font-display);
  font-size: 3rem;
  font-weight: 700;
  color: var(--ink-strong);
  margin-bottom: 0.1rem;
  line-height: 1;
  letter-spacing: 0.005em;
}

.page-header p {
  font-family: var(--font-hand);
  color: var(--ink-muted);
  font-size: 1.1rem;
}

/* ------------------ Stat Cards ------------------ */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 1.25rem;
  margin-bottom: 1.75rem;
}

.stat-card {
  background: var(--paper-surface);
  padding: 1.1rem 1.3rem 1.25rem;
  border: 2px solid var(--pencil);
  border-radius: 14px 11px 13px 12px;
  box-shadow: var(--shadow-soft);
  transform: rotate(-0.2deg);
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}

.stat-card:nth-child(even) {
  transform: rotate(0.25deg);
  border-radius: 12px 14px 11px 13px;
}

.stat-card:nth-child(3n) {
  transform: rotate(-0.35deg);
  border-radius: 13px 12px 14px 11px;
}

.stat-card:hover {
  transform: translateY(-2px) rotate(0deg);
  box-shadow: var(--shadow-lift);
  border-color: var(--ink-strong);
}

.stat-label {
  font-family: var(--font-hand);
  color: var(--ink-muted);
  font-size: 0.95rem;
  font-weight: 400;
  letter-spacing: 0.02em;
  margin-bottom: 0.35rem;
  text-transform: none;
}

.stat-value {
  font-family: var(--font-display);
  font-size: 2.75rem;
  font-weight: 700;
  color: var(--ink-strong);
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.stat-card.warning .stat-value { color: var(--warning); }
.stat-card.success .stat-value { color: var(--success); }
.stat-card.danger  .stat-value { color: var(--danger); }
.stat-card.info    .stat-value { color: var(--info); }

/* ------------------ Cards ------------------ */
.card {
  background: var(--paper-surface);
  border: 2px solid var(--pencil);
  border-radius: 14px 12px 13px 15px;
  padding: 1.35rem 1.4rem;
  margin-bottom: 1.5rem;
  box-shadow: var(--shadow-soft);
  transform: rotate(-0.1deg);
}

.card:nth-of-type(even) {
  transform: rotate(0.15deg);
  border-radius: 13px 15px 12px 14px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px dashed var(--pencil);
  gap: 1rem;
  flex-wrap: wrap;
}

.card-title {
  font-family: var(--font-display);
  font-size: 1.7rem;
  font-weight: 700;
  color: var(--ink-strong);
  line-height: 1;
  letter-spacing: 0.005em;
}

/* ------------------ Tables (legible, Patrick Hand) ------------------ */
.table-container {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--font-hand);
  font-size: 1rem;
}

thead {
  background: var(--paper-surface-warm);
  border-top: 2px solid var(--pencil);
  border-bottom: 2px solid var(--pencil);
}

th {
  text-align: left;
  padding: 0.65rem 0.85rem;
  font-family: var(--font-hand);
  font-weight: 400;
  color: var(--ink-strong);
  font-size: 0.95rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

td {
  padding: 0.65rem 0.85rem;
  border-top: 1px dashed var(--pencil);
  color: var(--ink-body);
  font-size: 1rem;
  font-variant-numeric: tabular-nums;
  line-height: 1.35;
}

tbody tr {
  transition: background-color 0.15s ease;
}

tbody tr:hover {
  background: var(--row-hover);
}

tbody tr:hover td {
  color: var(--ink-strong);
}

/* ------------------ Badges (hand-drawn pills) ------------------ */
.badge {
  display: inline-block;
  padding: 0.18rem 0.7rem 0.22rem;
  border-radius: 999px 12px 999px 14px;
  font-family: var(--font-hand);
  font-size: 0.9rem;
  font-weight: 400;
  letter-spacing: 0.02em;
  border: 1.5px solid currentColor;
  background: var(--paper-surface);
  transform: rotate(-1deg);
  white-space: nowrap;
  line-height: 1.2;
  text-transform: lowercase;
}

.badge + .badge {
  margin-left: 0.25rem;
}

.badge.success,
.badge.increasing {
  color: var(--success);
  background: #e9f4ec;
  transform: rotate(-1.2deg);
}

.badge.warning,
.badge.medium {
  color: var(--warning);
  background: #fbeed5;
  transform: rotate(0.8deg);
}

.badge.danger,
.badge.decreasing,
.badge.high {
  color: var(--danger);
  background: #f7e0dc;
  transform: rotate(-0.6deg);
}

.badge.info,
.badge.low,
.badge.stable {
  color: var(--info);
  background: #e3edf8;
  transform: rotate(1deg);
}

/* ------------------ Loading / Error ------------------ */
.loading {
  text-align: center;
  padding: 3rem;
  color: var(--ink-muted);
  font-family: var(--font-display);
  font-size: 1.6rem;
  letter-spacing: 0.01em;
}

.error {
  background: #f7e0dc;
  border: 2px solid var(--danger);
  border-radius: 12px 14px 11px 13px;
  color: var(--danger);
  padding: 1rem 1.25rem;
  margin: 1rem 0;
  font-family: var(--font-hand);
  font-size: 1.05rem;
  transform: rotate(-0.2deg);
}

/* ------------------ Global form controls (paper + pencil) ------------------ */
input[type="text"],
input[type="number"],
input[type="search"],
input[type="email"],
input[type="date"],
input[type="password"],
select,
textarea {
  font-family: var(--font-hand);
  font-size: 1rem;
  color: var(--ink-strong);
  background: var(--paper-surface);
  border: 1.5px solid var(--pencil);
  border-radius: 8px 10px 7px 9px;
  padding: 0.4rem 0.7rem;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

input[type="text"]:hover,
input[type="number"]:hover,
input[type="search"]:hover,
select:hover,
textarea:hover {
  border-color: var(--ink-muted);
}

input[type="text"]:focus,
input[type="number"]:focus,
input[type="search"]:focus,
select:focus,
textarea:focus {
  outline: none;
  border-color: var(--ink-blue);
  box-shadow: 0 0 0 3px rgba(59, 111, 179, 0.12);
}

button {
  font-family: var(--font-hand);
  font-size: 1rem;
}

/* ------------------ Theme toggle (notebook tab) ------------------ */
.theme-toggle {
  background: var(--paper-surface);
  color: var(--ink-body);
  border: 2px solid var(--pencil);
  padding: 0.4rem 0.95rem 0.45rem;
  border-radius: 9px 11px 8px 10px;
  font-family: var(--font-hand);
  font-size: 1rem;
  cursor: pointer;
  margin-right: 0.75rem;
  flex-shrink: 0;
  transform: rotate(-0.4deg);
  transition: background 0.15s ease, color 0.15s ease, transform 0.15s ease, box-shadow 0.15s ease;
}
.theme-toggle:hover {
  background: var(--highlighter);
  color: var(--ink-strong);
  transform: rotate(0deg) translateY(-1px);
  box-shadow: var(--shadow-soft);
}
.theme-toggle:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px rgba(59, 111, 179, 0.18);
}

/* ------------------ Range input (hand-drawn slider) ------------------
   Native range inputs ignore generic input styles; track + thumb need their
   own webkit/moz pseudo-elements. Track is a wavy pencil line, thumb is a
   rough ink circle with a soft scribble shadow. */
input[type="range"] {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 28px;
  background: transparent;
  cursor: grab;
  margin: 0;
  padding: 0;
}
input[type="range"]:active { cursor: grabbing; }
input[type="range"]:focus { outline: none; }

/* Hand-drawn track that splits at --fill-percent: inky wavy line on the
   filled side, pencil-tone dashes on the unfilled side. Components drive
   --fill-percent via :style on the range input. */
input[type="range"] {
  --fill-percent: 0%;
}
input[type="range"]::-webkit-slider-runnable-track {
  height: 8px;
  background:
    /* wavy ink line, clipped to the filled portion */
    linear-gradient(to right,
      var(--ink-blue) 0,
      var(--ink-blue) var(--fill-percent),
      transparent var(--fill-percent),
      transparent 100%) center / 100% 2px no-repeat,
    /* pencil-tone dashed line, full width as the unfilled rail */
    linear-gradient(to right,
      var(--pencil) 0,
      var(--pencil) 6px,
      transparent 6px,
      transparent 10px) center / 10px 1.5px repeat-x;
  border-radius: 4px 5px 3px 4px;
}
input[type="range"]::-moz-range-track {
  height: 8px;
  background:
    linear-gradient(to right,
      var(--ink-blue) 0,
      var(--ink-blue) var(--fill-percent),
      transparent var(--fill-percent),
      transparent 100%) center / 100% 2px no-repeat,
    linear-gradient(to right,
      var(--pencil) 0,
      var(--pencil) 6px,
      transparent 6px,
      transparent 10px) center / 10px 1.5px repeat-x;
  border-radius: 4px 5px 3px 4px;
  border: none;
}

/* Rough ink-circle thumb */
input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 22px;
  height: 22px;
  margin-top: -7px; /* center on 8px track */
  background: var(--paper-surface);
  border: 2px solid var(--ink-blue);
  border-radius: 50% 48% 52% 49%;
  box-shadow:
    0 1px 0 var(--ink-blue),
    0 0 0 3px rgba(59, 111, 179, 0.10),
    1px 2px 4px rgba(43, 42, 38, 0.15);
  transform: rotate(-3deg);
  transition: transform 0.12s ease, box-shadow 0.15s ease;
  cursor: grab;
}
input[type="range"]::-moz-range-thumb {
  width: 22px;
  height: 22px;
  background: var(--paper-surface);
  border: 2px solid var(--ink-blue);
  border-radius: 50% 48% 52% 49%;
  box-shadow:
    0 1px 0 var(--ink-blue),
    0 0 0 3px rgba(59, 111, 179, 0.10),
    1px 2px 4px rgba(43, 42, 38, 0.15);
  transform: rotate(-3deg);
  transition: transform 0.12s ease, box-shadow 0.15s ease;
  cursor: grab;
}
input[type="range"]:hover::-webkit-slider-thumb,
input[type="range"]:focus::-webkit-slider-thumb {
  transform: rotate(-3deg) scale(1.08);
  box-shadow:
    0 1px 0 var(--ink-blue),
    0 0 0 5px rgba(59, 111, 179, 0.15),
    1px 3px 6px rgba(43, 42, 38, 0.20);
}
input[type="range"]:hover::-moz-range-thumb,
input[type="range"]:focus::-moz-range-thumb {
  transform: rotate(-3deg) scale(1.08);
  box-shadow:
    0 1px 0 var(--ink-blue),
    0 0 0 5px rgba(59, 111, 179, 0.15),
    1px 3px 6px rgba(43, 42, 38, 0.20);
}
input[type="range"]:active::-webkit-slider-thumb {
  cursor: grabbing;
  transform: rotate(-1deg) scale(1.05);
}
input[type="range"]:active::-moz-range-thumb {
  cursor: grabbing;
  transform: rotate(-1deg) scale(1.05);
}

/* ------------------ Scrollbar polish (subtle) ------------------ */
::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: var(--pencil-soft);
  border-radius: 999px;
  border: 2px solid var(--paper-bg);
}

::-webkit-scrollbar-thumb:hover {
  background: var(--pencil);
}

/* ------------------ Selection ------------------ */
::selection {
  background: var(--highlighter);
  color: var(--ink-strong);
}
</style>
