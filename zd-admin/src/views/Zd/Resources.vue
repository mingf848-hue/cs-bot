<script setup lang="ts">
import { onMounted } from 'vue'
import { useZd } from './useZd'

const { state, ensureLoaded, save } = useZd()

onMounted(ensureLoaded)

const addGroup = () => state.config.resources.groups.push({ id: '', name: '新群组' })
const addPrefix = () => state.config.resources.sender_prefixes.push({ value: '', label: '新名单' })
</script>

<template>
  <div v-loading="state.loading" class="zd-page">
    <div class="zd-toolbar">
      <div>
        <div class="zd-title">资源管理</div>
        <div class="zd-subtitle">维护监听群、群名、允许/排除名单资源</div>
      </div>
      <ElButton type="primary" :loading="state.saving" @click="save">保存配置</ElButton>
    </div>

    <ElRow :gutter="12">
      <ElCol :span="12">
        <ElCard shadow="never">
          <template #header>
            <div class="card-header">
              <span>监听群资源</span>
              <ElButton size="small" type="primary" @click="addGroup">添加群</ElButton>
            </div>
          </template>
          <ElTable :data="state.config.resources.groups" height="620" size="small">
            <ElTableColumn label="群名" min-width="180">
              <template #default="{ row }"><ElInput v-model="row.name" /></template>
            </ElTableColumn>
            <ElTableColumn label="群 ID" min-width="220">
              <template #default="{ row }"><ElInput v-model="row.id" /></template>
            </ElTableColumn>
            <ElTableColumn width="80" align="center">
              <template #default="{ $index }">
                <ElButton
                  link
                  type="danger"
                  @click="state.config.resources.groups.splice($index, 1)"
                >
                  删除
                </ElButton>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </ElCol>
      <ElCol :span="12">
        <ElCard shadow="never">
          <template #header>
            <div class="card-header">
              <span>允许/排除名单资源</span>
              <ElButton size="small" type="primary" @click="addPrefix">添加名单</ElButton>
            </div>
          </template>
          <ElTable :data="state.config.resources.sender_prefixes" height="620" size="small">
            <ElTableColumn label="名称" min-width="180">
              <template #default="{ row }"><ElInput v-model="row.label" /></template>
            </ElTableColumn>
            <ElTableColumn label="匹配值" min-width="220">
              <template #default="{ row }"><ElInput v-model="row.value" /></template>
            </ElTableColumn>
            <ElTableColumn width="80" align="center">
              <template #default="{ $index }">
                <ElButton
                  link
                  type="danger"
                  @click="state.config.resources.sender_prefixes.splice($index, 1)"
                >
                  删除
                </ElButton>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </ElCol>
    </ElRow>
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
