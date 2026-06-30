<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { usePermissionStore } from '@/store/modules/permission'

defineOptions({ name: 'ZdLayout' })

const route = useRoute()
const permissionStore = usePermissionStore()

const menus = computed(() =>
  permissionStore.getRouters
    .filter((item) => !item.meta?.hidden)
    .map((item) => ({
      path: item.path,
      title: String(item.meta?.title || item.name || item.path)
    }))
)
</script>

<template>
  <div class="zd-layout">
    <aside class="zd-sidebar">
      <div class="brand">ZD Admin</div>
      <nav>
        <RouterLink
          v-for="item in menus"
          :key="item.path"
          :to="item.path"
          :class="{ active: route.path === item.path || route.path.startsWith(`${item.path}/`) }"
        >
          {{ item.title }}
        </RouterLink>
      </nav>
    </aside>
    <main class="zd-main">
      <div class="zd-topbar">
        <strong>{{ route.meta?.title || 'ZD Admin' }}</strong>
      </div>
      <section class="zd-content">
        <RouterView />
      </section>
    </main>
  </div>
</template>

<style scoped>
.zd-layout {
  display: grid;
  min-height: 100vh;
  grid-template-columns: 216px minmax(0, 1fr);
  background: #f5f7fa;
}

.zd-sidebar {
  border-right: 1px solid #e5e7eb;
  background: #111827;
  color: #fff;
}

.brand {
  display: flex;
  align-items: center;
  height: 56px;
  padding: 0 18px;
  border-bottom: 1px solid rgb(255 255 255 / 10%);
  font-size: 16px;
  font-weight: 700;
}

nav {
  display: grid;
  gap: 4px;
  padding: 12px;
}

nav a {
  display: flex;
  align-items: center;
  height: 38px;
  padding: 0 12px;
  border-radius: 6px;
  color: #cbd5e1;
  font-size: 14px;
  text-decoration: none;
}

nav a:hover,
nav a.active {
  background: #2563eb;
  color: #fff;
}

.zd-main {
  min-width: 0;
}

.zd-topbar {
  display: flex;
  align-items: center;
  height: 56px;
  padding: 0 20px;
  border-bottom: 1px solid #e5e7eb;
  background: #fff;
  color: #111827;
}

.zd-content {
  padding: 16px;
}
</style>
