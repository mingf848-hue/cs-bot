<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElAlert, ElButton, ElCard, ElCheckbox, ElInput } from 'element-plus'
import { useRouter } from 'vue-router'
import type { RouteLocationNormalizedLoaded, RouteRecordRaw } from 'vue-router'
import { loginApi } from '@/api/login'
import { usePermissionStore } from '@/store/modules/permission'
import { useUserStore } from '@/store/modules/user'

defineOptions({ name: 'Login' })

const userStore = useUserStore()
const permissionStore = usePermissionStore()
const { currentRoute, addRoute, push } = useRouter()

const form = reactive({
  username: userStore.getLoginInfo || 'changshan',
  password: ''
})

const remember = ref(userStore.getRememberMe)
const loading = ref(false)
const errorMessage = ref('')
const redirect = ref('')

const canSubmit = computed(() => form.username.trim() && form.password.trim())

watch(
  () => currentRoute.value,
  (route: RouteLocationNormalizedLoaded) => {
    redirect.value = route?.query?.redirect as string
  },
  { immediate: true }
)

watch(remember, (value) => {
  userStore.setRememberMe(value)
  if (!value) userStore.setLoginInfo(undefined)
})

const signIn = async () => {
  if (!canSubmit.value || loading.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    const res = await loginApi({ username: form.username.trim(), password: form.password })
    if (remember.value) userStore.setLoginInfo(form.username.trim())
    else userStore.setLoginInfo(undefined)
    userStore.setRememberMe(remember.value)
    userStore.setUserInfo(res.data)
    await permissionStore.generateRoutes('static')
    permissionStore.getAddRouters.forEach((route) => addRoute(route as RouteRecordRaw))
    permissionStore.setIsAddRouters(true)
    push({ path: redirect.value || permissionStore.addRouters[0].path })
  } catch (error: any) {
    errorMessage.value = error?.message || '登录失败，请检查用户名和密码'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <ElCard class="login-card" shadow="never">
      <div class="brand">
        <img src="@/assets/imgs/logo.png" alt="ZD" />
        <div>
          <h1>ZD Admin</h1>
          <p>监控后台</p>
        </div>
      </div>

      <ElAlert
        v-if="errorMessage"
        class="mb-16px"
        :title="errorMessage"
        type="error"
        show-icon
        :closable="false"
      />

      <div class="form-stack">
        <label>
          <span>用户名</span>
          <ElInput v-model="form.username" size="large" placeholder="changshan" autofocus />
        </label>
        <label>
          <span>密码</span>
          <ElInput
            v-model="form.password"
            size="large"
            type="password"
            show-password
            placeholder="请输入密码"
            @keydown.enter="signIn"
          />
        </label>
        <div class="login-options">
          <ElCheckbox v-model="remember">记住用户名</ElCheckbox>
        </div>
        <ElButton
          type="primary"
          size="large"
          :loading="loading"
          :disabled="!canSubmit"
          @click="signIn"
        >
          登录
        </ElButton>
      </div>
    </ElCard>
  </div>
</template>

<style scoped>
.login-page {
  display: grid;
  min-height: 100vh;
  place-items: center;
  background: #f3f5f8;
}

.login-card {
  width: 380px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 22px;
}

.brand img {
  width: 42px;
  height: 42px;
}

.brand h1 {
  margin: 0;
  color: #111827;
  font-size: 20px;
  font-weight: 700;
}

.brand p {
  margin: 3px 0 0;
  color: #6b7280;
  font-size: 13px;
}

.form-stack {
  display: grid;
  gap: 14px;
}

.form-stack label {
  display: grid;
  gap: 7px;
}

.form-stack label span {
  color: #374151;
  font-size: 13px;
  font-weight: 600;
}

.login-options {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
