<script setup lang="ts">
import { onMounted } from 'vue'
import { ElButton, ElCard, ElInput, ElTable, ElTableColumn } from 'element-plus'
import { useZd } from './useZd'

const { state, ensureLoaded, refresh, save } = useZd()

defineOptions({ name: 'ZdResources' })

onMounted(ensureLoaded)

const addPrefix = () => state.config.resources.sender_prefixes.push({ value: '', label: '新名单' })
const saveAndRefresh = async () => {
  await save()
  await refresh()
}
</script>

<template>
  <div v-loading="state.loading" class="zd-page">
    <div class="zd-toolbar">
      <div>
        <div class="zd-title">资源管理</div>
        <div class="zd-subtitle">维护允许/排除名单资源</div>
      </div>
      <ElButton type="primary" :loading="state.saving" @click="saveAndRefresh">保存配置</ElButton>
    </div>

    <ElCard shadow="never">
      <template #header>
        <div class="card-header">
          <span>允许/排除名单资源</span>
          <ElButton size="small" type="primary" @click="addPrefix">添加名单</ElButton>
        </div>
      </template>
      <ElTable :data="state.config.resources.sender_prefixes" height="620" size="small">
        <ElTableColumn label="名称" min-width="180">
          <template #default="scope"
            ><ElInput v-if="scope?.row" v-model="scope.row.label"
          /></template>
        </ElTableColumn>
        <ElTableColumn label="匹配值" min-width="220">
          <template #default="scope"
            ><ElInput v-if="scope?.row" v-model="scope.row.value"
          /></template>
        </ElTableColumn>
        <ElTableColumn width="80" align="center">
          <template #default="scope">
            <ElButton
              v-if="scope?.$index !== undefined"
              link
              type="danger"
              @click="state.config.resources.sender_prefixes.splice(scope.$index, 1)"
            >
              删除
            </ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>
  </div>
</template>

<style scoped>
.zd-page {
  display: grid;
  gap: 12px;
}
.zd-toolbar,
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.zd-title {
  font-size: 20px;
  font-weight: 700;
}
.zd-subtitle {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
