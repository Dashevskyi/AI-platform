import { useMemo } from 'react';
import { useAuth } from './useAuth';

export type Permission =
  | 'tools'
  | 'data_sources'
  | 'keys'
  | 'model_config'
  | 'shell_config'
  | 'kb'
  | 'memory'
  | 'chats'
  | 'logs'
  | 'users';

export const ALL_PERMISSIONS: Permission[] = [
  'tools',
  'data_sources',
  'keys',
  'model_config',
  'shell_config',
  'kb',
  'memory',
  'chats',
  'logs',
  'users',
];

export const PERMISSION_LABELS: Record<Permission, string> = {
  tools: 'Tools',
  data_sources: 'Data sources',
  keys: 'API ключи',
  model_config: 'Модели',
  shell_config: 'Настройки шелла',
  kb: 'Knowledge base',
  memory: 'Память',
  chats: 'Чаты',
  logs: 'Логи',
  users: 'Пользователи',
};

export function usePermissions() {
  const { user } = useAuth();
  return useMemo(() => {
    const role = user?.role || '';
    const isSuperadmin = role === 'superadmin';
    const isTenantAdmin = role === 'tenant_admin';
    const tenantId = user?.tenant_id || null;
    const perms = new Set<string>(user?.permissions || []);
    const has = (p: Permission) => isSuperadmin || perms.has(p);
    return {
      role,
      isSuperadmin,
      isTenantAdmin,
      tenantId,
      permissions: Array.from(perms),
      has,
    };
  }, [user]);
}
