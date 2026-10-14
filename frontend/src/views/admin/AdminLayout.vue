<script setup>
/**
 * 管理后台外层布局（7.1：管理页面与普通用户页面在导航中明显区分）。
 */
</script>

<template>
  <div>
    <div class="tabs">
      <router-link :to="{ name: 'admin-overview' }">管理概览</router-link>
      <router-link :to="{ name: 'admin-users' }">用户管理</router-link>
      <router-link :to="{ name: 'admin-datasets' }">数据集管理</router-link>
      <router-link :to="{ name: 'admin-models' }">模型管理</router-link>
      <router-link :to="{ name: 'admin-logs' }">操作日志</router-link>
    </div>
    <router-view />
  </div>
</template>
