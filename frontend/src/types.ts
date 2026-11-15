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
/** 知识目录元数据不包含正文，旧文本条目可以没有活动版本。 */
export interface KnowledgeMetadata {
  id: number
  title: string
  acl_version: number
  is_active: boolean
  active_version_id: number | null
  category: string | null
  updated_at: string | null
  format: string | null
}
export interface KnowledgeManagedMetadata extends KnowledgeMetadata { acl: AclEntry[]; rebuild_task_id?: string | null }
export interface KnowledgeDocument extends KnowledgeMetadata { content: string }
export interface KnowledgeCreate { title: string; content: string; acl: AclEntry[] }
export interface KnowledgePatch { title?: string; content?: string; is_active?: boolean; category?: string }
/** 导入状态和进度直接采用数据库任务响应。 */
export type ImportStatus = 'queued' | 'running' | 'succeeded' | 'partial' | 'failed'
export interface ImportTaskItem {
  id: string
  relative_path: string
  status: ImportStatus
  stage: string
  progress: number
  error: string | null
  knowledge_id: number | null
  version_id: number | null
  attempt: number
}
export interface ImportTask { id: string; status: ImportStatus; total: number; completed: number; failed: number; items: ImportTaskItem[] }
export interface KnowledgeVersion { id: number; number: number; title: string; status: string; created_at: string; error: string | null; file_format: string; sha256: string | null }
export interface KnowledgeChunk { id: number; version_id: number; sequence: number; content: string; section: string | null; page: number | null; location: string | null; token_count: number }
export interface SearchItem { knowledge_id: number; version_id: number; chunk_id: number; title: string; section: string | null; page: number | null; location: string | null; content: string; score: number }
export interface SearchResponse { items: SearchItem[]; restricted: boolean; message?: string }
export interface KnowledgeDownload { blob: Blob; filename: string | null }
export interface UserWrite {
  display_name: string
  department_id: number | null
  role_ids: number[]
  is_active: boolean
  password?: string
}
