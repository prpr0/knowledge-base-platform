<!-- 编辑全局、直属部门、角色和个人四维知识授权。 -->
<script setup lang="ts">
import { computed } from 'vue'
import type { AclEntry, AclType, Department, Role, User } from '../types'
const props = defineProps<{ modelValue: AclEntry[]; departments: Department[]; roles: Role[]; users: User[]; canReadDepartments: boolean; canReadRoles: boolean; canReadUsers: boolean; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: AclEntry[]] }>()
const global = computed(() => props.modelValue.some((item) => item.type === 'global'))
/** 四维访问采用 OR，仅提交明确选择的实体，不继承父部门。 */
function selected(type: AclType): number[] { return props.modelValue.filter((item) => item.type === type && item.entity_id !== null).map((item) => item.entity_id!) }
/** 只替换当前维度，完整保留其余真实授权。 */
function change(type: AclType, ids: number[]): void {
  emit('update:modelValue', [...props.modelValue.filter((item) => item.type !== type), ...[...new Set(ids)].map((entity_id) => ({ type, entity_id }))])
}
/** 全局授权使用协议指定的 null，不从管理角色推导。 */
function changeGlobal(enabled: boolean): void { emit('update:modelValue', [...props.modelValue.filter((item) => item.type !== 'global'), ...(enabled ? [{ type: 'global' as const, entity_id: null }] : [])]) }
</script>
<template>
  <div class="acl-editor"><el-alert title="任意一项命中即可读取。空授权默认拒绝所有人；读取仍需 knowledge:read 权限。" type="info" :closable="false" show-icon /><div class="acl-global"><el-checkbox :model-value="global" :disabled="disabled" @update:model-value="changeGlobal(Boolean($event))">全局授权</el-checkbox><span class="field-hint">允许所有具备知识读取权限的账号访问</span></div>
  <el-form-item label="部门授权（仅直属部门）"><el-select :model-value="selected('department')" multiple filterable clearable placeholder="选择获准部门" :disabled="disabled || !canReadDepartments" class="full-width" @update:model-value="change('department', $event)"><el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id" /><el-option v-for="id in selected('department').filter((id) => !departments.some((item) => item.id === id))" :key="id" :label="'部门 #' + id" :value="id" /></el-select><p v-if="!canReadDepartments" class="field-hint">需组织查看或部门管理权限读取候选部门，已有授权保留。</p></el-form-item>
  <el-form-item label="角色授权"><el-select :model-value="selected('role')" multiple filterable clearable placeholder="选择获准角色" :disabled="disabled || !canReadRoles" class="full-width" @update:model-value="change('role', $event)"><el-option v-for="item in roles" :key="item.id" :label="item.name" :value="item.id" /><el-option v-for="id in selected('role').filter((id) => !roles.some((item) => item.id === id))" :key="id" :label="'角色 #' + id" :value="id" /></el-select><p v-if="!canReadRoles" class="field-hint">需组织查看或角色管理权限读取候选角色，已有授权保留。</p></el-form-item>
  <el-form-item label="个人授权"><el-select :model-value="selected('user')" multiple filterable clearable placeholder="选择获准用户" :disabled="disabled || !canReadUsers" class="full-width" @update:model-value="change('user', $event)"><el-option v-for="item in users" :key="item.id" :label="item.display_name + '（' + item.username + '）'" :value="item.id" /><el-option v-for="id in selected('user').filter((id) => !users.some((item) => item.id === id))" :key="id" :label="'用户 #' + id" :value="id" /></el-select><p v-if="!canReadUsers" class="field-hint">需组织查看或用户管理权限读取候选账号，已有授权保留。</p></el-form-item></div>
</template>
