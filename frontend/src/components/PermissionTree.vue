<!-- 显示互不隐含授权的菜单与按钮权限树。 -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ElTree } from 'element-plus'
import type { Permission } from '../types'
const props = defineProps<{ modelValue: string[]; permissions: Permission[]; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: string[]] }>()
const tree = ref<InstanceType<typeof ElTree>>()
interface Node { code: string; label: string; disabled: boolean; children: Node[] }
/** 按服务端提供的父节点构造权限树，勾选不推导任何隐含授权。 */
const nodes = computed(() => {
  const map = new Map<string, Node>(props.permissions.map((item) => [item.code, { code: item.code, label: item.label + ' · ' + item.code, disabled: !!props.disabled, children: [] }]))
  const roots: Node[] = []
  for (const item of props.permissions) {
    const node = map.get(item.code)!
    const parent = item.parent ? map.get(item.parent) : undefined
    if (parent && parent !== node) parent.children.push(node)
    else roots.push(node)
  }
  return roots
})
/** 同步服务端已保存权限，不自动选中父级或子级。 */
watch([() => props.modelValue, nodes], async () => { await nextTick(); tree.value?.setCheckedKeys(props.modelValue) }, { immediate: true, deep: true })
/** 仅提交具体勾选的权限码。 */
function update(): void { emit('update:modelValue', (tree.value?.getCheckedKeys() ?? []).map(String)) }
</script>
<template><el-tree ref="tree" class="permission-tree" :data="nodes" node-key="code" :props="{ label: 'label', children: 'children', disabled: 'disabled' }" :default-checked-keys="modelValue" check-strictly show-checkbox default-expand-all @check="update" /><p class="field-hint">菜单与操作权限分别勾选，父子节点互不隐含授权。</p></template>
