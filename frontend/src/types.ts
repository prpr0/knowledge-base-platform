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

/** 会话和引用均来自服务端当前权限核验。 */
export interface Citation { label: string; knowledge_id: number; version_id: number; chunk_id: number; title: string; section: string; page: number | null; location: string; content?: string }
export interface Conversation { id: number; title: string; created_at: string; updated_at: string }
export interface ChatMessage { id: number; role: string; content: string; status: string; citations: Citation[]; model_version: string | null; source_mode: string | null }
export interface Candidate { id: number; question: string; answer: string; aliases: string[]; sources: Citation[]; count: number; confidence: number; status: string }
export interface FaqRelease { id: number; question: string; answer: string; version: number; enabled: boolean; sources: Citation[] }
export interface Gap { id: number; question: string; frequency: number; status: string; highest_score: number | null }
export interface Supplement { id: number; gap_id: number; title: string; assigned_to: number | null; status: string; knowledge_ids: number[] }
export interface MiningRun { id: string; status: string; count: number; error: string | null }
export interface ModelSettings { base_url: string; model_name: string; has_api_key: boolean; model_version: string | null; release_verified: boolean; calibration_verified: boolean; status: string; configuration_id: string }
export interface Metrics { pv: number; uv: number; knowledge_count: number; faq_hit_rate: number; coverage_rate: number; permission_block_rate: number; token_total: number; token_unknown_requests: number; failures: number; latency: { mean_ms: number | null; p50_ms: number | null; p95_ms: number | null }; top_questions: { question: string; count: number }[]; top_knowledge: { id: number; title: string; count: number }[]; daily: { date: string; pv: number; uv: number; tokens: number; latency_ms: number | null }[] }
export interface Audit { request_id: string; user_id: number | null; question: string; candidates: number[]; allowed: number[]; blocked: number[]; status: string; source_mode: string; model_called: boolean; token_usage: Record<string, number> | null; latency_ms: number | null; error_code: string | null }
