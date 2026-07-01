<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import {
  ElAvatar,
  ElButton,
  ElDialog,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage
} from 'element-plus'
import { usePermissionStore } from '@/store/modules/permission'
import { useLockStore } from '@/store/modules/lock'
import { useUserStore } from '@/store/modules/user'

defineOptions({ name: 'ZdLayout' })

const route = useRoute()
const permissionStore = usePermissionStore()
const lockStore = useLockStore()
const userStore = useUserStore()
const lockDialogVisible = ref(false)
const lockPassword = ref('')
const unlockPassword = ref('')

const menus = computed(() =>
  permissionStore.getRouters
    .filter((item) => !item.meta?.hidden)
    .map((item) => ({
      path: item.path,
      title: String(item.meta?.title || item.name || item.path)
    }))
)

const username = computed(() => userStore.getUserInfo?.username || 'changshan')
const avatarText = computed(() => username.value.trim().slice(0, 1).toUpperCase() || 'C')
const isLocked = computed(() => lockStore.getLockInfo?.isLock === true)

const openLockDialog = () => {
  lockPassword.value = ''
  lockDialogVisible.value = true
}

const lockScreen = () => {
  if (!lockPassword.value.trim()) {
    ElMessage.warning('请输入锁屏密码')
    return
  }
  lockStore.setLockInfo({ isLock: true, password: lockPassword.value.trim() })
  lockDialogVisible.value = false
  unlockPassword.value = ''
}

const unlockScreen = async () => {
  const ok = await lockStore.unLock(unlockPassword.value.trim())
  if (ok) {
    unlockPassword.value = ''
    ElMessage.success('已解锁')
  } else {
    ElMessage.error('锁屏密码错误')
  }
}
</script>

<template>
  <div class="zd-layout">
    <aside class="zd-sidebar">
      <div class="brand">
        <span class="brand-mark">ZD</span>
        <span>ZD Admin</span>
      </div>
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
        <div class="top-actions">
          <ElButton text class="lock-btn" @click="openLockDialog">锁屏</ElButton>
          <ElDropdown trigger="click">
            <button class="user-chip" type="button">
              <ElAvatar :size="30" class="avatar">{{ avatarText }}</ElAvatar>
              <span>{{ username }}</span>
            </button>
            <template #dropdown>
              <ElDropdownMenu>
                <ElDropdownItem disabled>当前账号：{{ username }}</ElDropdownItem>
                <ElDropdownItem divided @click="openLockDialog">锁定屏幕</ElDropdownItem>
              </ElDropdownMenu>
            </template>
          </ElDropdown>
        </div>
      </div>
      <section class="zd-content">
        <RouterView />
      </section>
    </main>

    <ElDialog v-model="lockDialogVisible" title="锁定屏幕" width="360px">
      <ElForm label-position="top" @submit.prevent>
        <ElFormItem label="锁屏密码">
          <ElInput
            v-model="lockPassword"
            type="password"
            show-password
            autofocus
            placeholder="输入本次解锁密码"
            @keydown.enter="lockScreen"
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="lockDialogVisible = false">取消</ElButton>
        <ElButton type="primary" @click="lockScreen">锁定</ElButton>
      </template>
    </ElDialog>

    <div v-if="isLocked" class="lock-screen">
      <div class="lock-panel">
        <ElAvatar :size="60" class="lock-avatar">{{ avatarText }}</ElAvatar>
        <h2>{{ username }}</h2>
        <p>屏幕已锁定</p>
        <ElInput
          v-model="unlockPassword"
          type="password"
          show-password
          placeholder="输入锁屏密码"
          @keydown.enter="unlockScreen"
        />
        <ElButton type="primary" class="unlock-button" @click="unlockScreen">进入系统</ElButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.zd-layout {
  display: grid;
  min-height: 100vh;
  height: 100vh;
  grid-template-columns: 216px minmax(0, 1fr);
  background: #f5f7fa;
  overflow: hidden;
}

.zd-sidebar {
  border-right: 1px solid #e5e7eb;
  background: #111827;
  color: #fff;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 56px;
  padding: 0 18px;
  border-bottom: 1px solid rgb(255 255 255 / 10%);
  font-size: 16px;
  font-weight: 700;
}

.brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: #2563eb;
  color: #fff;
  font-size: 12px;
  font-weight: 800;
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
  display: flex;
  min-height: 0;
  flex-direction: column;
}

.zd-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  flex: 0 0 56px;
  padding: 0 20px;
  border-bottom: 1px solid #e5e7eb;
  background: #fff;
  color: #111827;
}

.top-actions,
.user-chip {
  display: flex;
  align-items: center;
  gap: 10px;
}

.lock-btn {
  color: #475569;
}

.user-chip {
  height: 38px;
  border: 0;
  border-radius: 19px;
  background: #f1f5f9;
  padding: 4px 10px 4px 4px;
  color: #111827;
  cursor: pointer;
}

.avatar,
.lock-avatar {
  background: #2563eb;
  color: #fff;
  font-weight: 700;
}

.zd-content {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 16px;
}

.lock-screen {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgb(15 23 42 / 82%);
  backdrop-filter: blur(10px);
}

.lock-panel {
  width: 340px;
  border-radius: 12px;
  background: #fff;
  padding: 28px;
  text-align: center;
  box-shadow: 0 24px 80px rgb(0 0 0 / 30%);
}

.lock-panel h2 {
  margin: 14px 0 4px;
  font-size: 20px;
}

.lock-panel p {
  margin: 0 0 18px;
  color: #64748b;
}

.unlock-button {
  width: 100%;
  margin-top: 12px;
}
</style>
