/** 当前阶段真实 API 的传输数据。 */
export interface CurrentUser {
  id: number
  username: string
  display_name: string
  department_id: number | null
  role_ids: number[]
  permissions: string[]
  permission_version: number
}

export interface Department { id: number; name: string; parent_id: number | null }
export interface User {
  id: number
  username: string
  display_name: string
  department_id: number | null
  role_ids: number[]
  is_active: boolean
  permission_version: number
}
export interface Role { id: number; name: string; permissions: string[] }
export interface Permission { code: string; label: string; kind: string; parent: string | null }
export type AclType = 'global' | 'department' | 'role' | 'user'
export interface AclEntry { type: AclType; entity_id: number | null }
export interface KnowledgeMetadata { id: number; title: string; acl_version: number }
export interface KnowledgeManagedMetadata extends KnowledgeMetadata { acl: AclEntry[] }
export interface KnowledgeDocument extends KnowledgeMetadata { content: string }
export interface KnowledgeCreate { title: string; content: string; acl: AclEntry[] }
export interface KnowledgePatch { title?: string; content?: string }
export interface UserWrite {
  display_name: string
  department_id: number | null
  role_ids: number[]
  is_active: boolean
  password?: string
}
