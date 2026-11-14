/** 判定当前用户的菜单和按钮操作权限。 */
export function hasPermission(permissions: readonly string[], permission: string): boolean {
  /** 仅精确匹配服务端返回的权限码，管理标记不具备通配含义。 */
  return permissions.includes(permission)
}
