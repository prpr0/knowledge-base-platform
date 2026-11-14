<!-- 将部门父关系呈现为可选择的直属部门树。 -->
<script setup lang="ts">
import { computed } from 'vue'
import type { Department } from '../types'
const props = defineProps<{ modelValue: number | null; departments: Department[]; excludeId?: number; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: number | null] }>()
interface Node { value: number; label: string; disabled: boolean; children: Node[] }
/** 构建真实部门层级；编辑时禁用自身和所有子部门。 */
const options = computed(() => {
  const excluded = new Set<number>()
  if (props.excludeId !== undefined) {
    excluded.add(props.excludeId)
    let changed = true
    while (changed) {
      changed = false
      for (const item of props.departments) if (item.parent_id !== null && excluded.has(item.parent_id) && !excluded.has(item.id)) { excluded.add(item.id); changed = true }
    }
  }
  const nodes = new Map<number, Node>(props.departments.map((item) => [item.id, { value: item.id, label: item.name, disabled: excluded.has(item.id), children: [] }]))
  const roots: Node[] = []
  for (const item of props.departments) {
    const node = nodes.get(item.id)!
    const parent = item.parent_id === null ? undefined : nodes.get(item.parent_id)
    if (parent && parent !== node) parent.children.push(node)
    else roots.push(node)
  }
  return roots
})
/** 清空选择统一使用 API 约定的 null。 */
function change(value: number | undefined | null): void { emit('update:modelValue', value ?? null) }
</script>
<template><el-tree-select :model-value="modelValue" :data="options" :disabled="disabled" check-strictly clearable filterable :render-after-expand="false" placeholder="请选择部门（可留空）" class="full-width" @update:model-value="change" /></template>
