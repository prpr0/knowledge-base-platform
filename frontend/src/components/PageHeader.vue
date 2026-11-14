<!-- 呈现页面标题、说明与操作区。 -->
<script setup lang="ts">
defineProps<{ title: string; description: string }>()
</script>
<template><div class="page-heading"><div><div class="eyebrow">KNOWLEDGE WORKSPACE</div><h1>{{ title }}</h1><p>{{ description }}</p></div><div class="page-actions"><slot /></div></div></template>
