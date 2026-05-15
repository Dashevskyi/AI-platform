import { useState } from 'react';
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Center,
  Code,
  Group,
  Loader,
  Modal,
  NumberInput,
  Pagination,
  PasswordInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Textarea,
} from '@mantine/core';
import { IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { notifications } from '@mantine/notifications';
import { dataSourcesApi } from '../../shared/api/endpoints';
import type {
  TenantDataSource,
  TenantDataSourceCreate,
  TenantDataSourceUpdate,
} from '../../shared/api/types';

const DATA_SOURCE_KIND_OPTIONS = [
  { value: 'mariadb', label: 'MariaDB / MySQL' },
  { value: 'postgresql', label: 'PostgreSQL' },
  { value: 'http_api', label: 'HTTP API' },
  { value: 'ssh', label: 'SSH' },
  { value: 'telnet', label: 'Telnet' },
  { value: 'snmp', label: 'SNMP' },
];

const DB_KINDS = new Set(['mariadb', 'postgresql']);
const NET_KINDS = new Set(['ssh', 'telnet', 'snmp']);

type DataSourcesTabProps = {
  tenantId: string;
};

export function DataSourcesTab({ tenantId }: DataSourcesTabProps) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [modalOpen, setModalOpen] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [kind, setKind] = useState('mariadb');
  const [isActive, setIsActive] = useState(true);
  const [host, setHost] = useState('');
  const [port, setPort] = useState<number | ''>('');
  const [databaseName, setDatabaseName] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [replaceSecret, setReplaceSecret] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');
  const [authType, setAuthType] = useState('none');
  const [authHeaderName, setAuthHeaderName] = useState('X-API-Key');
  const [apiUsername, setApiUsername] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [snmpVersion, setSnmpVersion] = useState('2c');
  const [community, setCommunity] = useState('');
  const [privateKey, setPrivateKey] = useState('');
  const [initCommands, setInitCommands] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'data-sources', page],
    queryFn: () => dataSourcesApi.list(tenantId, page),
  });

  const resetForm = () => {
    setEditId(null);
    setName('');
    setDescription('');
    setKind('mariadb');
    setIsActive(true);
    setHost('');
    setPort('');
    setDatabaseName('');
    setUsername('');
    setPassword('');
    setReplaceSecret(false);
    setBaseUrl('');
    setAuthType('none');
    setAuthHeaderName('X-API-Key');
    setApiUsername('');
    setApiSecret('');
    setSnmpVersion('2c');
    setCommunity('');
    setPrivateKey('');
    setInitCommands('');
  };

  const openCreate = () => {
    resetForm();
    setModalOpen(true);
  };

  const openEdit = (ds: TenantDataSource) => {
    setEditId(ds.id);
    setName(ds.name);
    setDescription(ds.description || '');
    setKind(ds.kind);
    setIsActive(ds.is_active);
    setReplaceSecret(false);
    const cfg = ds.config_json || {};
    setHost(typeof cfg.host === 'string' ? cfg.host : '');
    setPort(typeof cfg.port === 'number' ? cfg.port : '');
    setDatabaseName(typeof cfg.database === 'string' ? cfg.database : '');
    setUsername(typeof cfg.username === 'string' ? cfg.username : '');
    setPassword('');
    setBaseUrl(typeof cfg.base_url === 'string' ? cfg.base_url : '');
    setAuthType(typeof cfg.auth_type === 'string' ? cfg.auth_type : 'none');
    setAuthHeaderName(typeof cfg.auth_header_name === 'string' ? cfg.auth_header_name : 'X-API-Key');
    setApiUsername(typeof cfg.username === 'string' ? cfg.username : '');
    setApiSecret('');
    setSnmpVersion(
      typeof cfg.snmp_version === 'string'
        ? cfg.snmp_version
        : (typeof cfg.version === 'string' ? cfg.version : '2c'),
    );
    setCommunity('');
    setPrivateKey('');
    setInitCommands(Array.isArray(cfg.init_commands) ? (cfg.init_commands as string[]).join('\n') : '');
    setModalOpen(true);
  };

  const createMutation = useMutation({
    mutationFn: (payload: TenantDataSourceCreate) => dataSourcesApi.create(tenantId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'data-sources'] });
      setModalOpen(false);
      notifications.show({ title: 'Создано', message: 'Источник данных создан', color: 'green' });
    },
    onError: (err: Error) => {
      notifications.show({
        title: 'Ошибка',
        message: err.message || 'Не удалось создать источник данных',
        color: 'red',
      });
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: TenantDataSourceUpdate }) =>
      dataSourcesApi.update(tenantId, id, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'data-sources'] });
      setModalOpen(false);
      notifications.show({ title: 'Обновлено', message: 'Источник данных обновлён', color: 'green' });
    },
    onError: (err: Error) => {
      notifications.show({
        title: 'Ошибка',
        message: err.message || 'Не удалось обновить источник данных',
        color: 'red',
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => dataSourcesApi.delete(tenantId, id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'data-sources'] });
      notifications.show({ title: 'Удалено', message: 'Источник данных удалён', color: 'green' });
    },
  });

  const handleSave = () => {
    let configJson: Record<string, unknown>;
    let secretJson: Record<string, unknown> | undefined;

    if (DB_KINDS.has(kind)) {
      if (!host.trim() || !databaseName.trim() || !username.trim()) {
        notifications.show({ title: 'Ошибка', message: 'Для БД нужны host, database и username', color: 'red' });
        return;
      }
      configJson = {
        host: host.trim(),
        ...(port ? { port: Number(port) } : {}),
        database: databaseName.trim(),
        username: username.trim(),
      };
      if (!editId || replaceSecret) {
        if (!password.trim()) {
          notifications.show({ title: 'Ошибка', message: 'Укажите пароль БД', color: 'red' });
          return;
        }
        secretJson = { password: password.trim() };
      }
    } else if (NET_KINDS.has(kind)) {
      if (!host.trim()) {
        notifications.show({ title: 'Ошибка', message: 'Укажите host', color: 'red' });
        return;
      }
      const defaultPort = kind === 'ssh' ? 22 : kind === 'telnet' ? 23 : 161;
      const initCmds = initCommands.split('\n').map((s) => s.trim()).filter(Boolean);
      configJson = {
        host: host.trim(),
        port: port ? Number(port) : defaultPort,
        ...(kind !== 'snmp' && username.trim() ? { username: username.trim() } : {}),
        ...(kind === 'snmp' ? { snmp_version: snmpVersion } : {}),
        ...(initCmds.length > 0 && kind !== 'snmp' ? { init_commands: initCmds } : {}),
      };
      if (!editId || replaceSecret) {
        if (kind === 'snmp') {
          if (!community.trim()) {
            notifications.show({ title: 'Ошибка', message: 'Укажите community string', color: 'red' });
            return;
          }
          secretJson = { community: community.trim() };
        } else {
          const sec: Record<string, unknown> = {};
          if (password.trim()) sec.password = password.trim();
          if (privateKey.trim()) sec.private_key = privateKey.trim();
          if (!sec.password && !sec.private_key) {
            notifications.show({ title: 'Ошибка', message: 'Укажите пароль или приватный ключ', color: 'red' });
            return;
          }
          secretJson = sec;
        }
      }
    } else {
      if (!baseUrl.trim()) {
        notifications.show({ title: 'Ошибка', message: 'Для API нужен base URL', color: 'red' });
        return;
      }
      configJson = {
        base_url: baseUrl.trim(),
        auth_type: authType,
        ...(authType === 'header' ? { auth_header_name: authHeaderName.trim() || 'X-API-Key' } : {}),
        ...(authType === 'basic' ? { username: apiUsername.trim() } : {}),
      };
      if (!editId || replaceSecret) {
        if (authType !== 'none' && !apiSecret.trim()) {
          notifications.show({ title: 'Ошибка', message: 'Укажите API secret/token', color: 'red' });
          return;
        }
        if (authType !== 'none') {
          secretJson = authType === 'basic'
            ? { password: apiSecret.trim() }
            : { token: apiSecret.trim() };
        } else {
          secretJson = {};
        }
      }
    }

    const basePayload = {
      name: name.trim(),
      description: description.trim() || undefined,
      kind,
      config_json: configJson,
      is_active: isActive,
    };
    if (!basePayload.name) {
      notifications.show({ title: 'Ошибка', message: 'Укажите название источника данных', color: 'red' });
      return;
    }
    if (editId) {
      const updatePayload: TenantDataSourceUpdate = {
        ...basePayload,
        ...(replaceSecret ? { secret_json: secretJson ?? null } : {}),
      };
      updateMutation.mutate({ id: editId, payload: updatePayload });
    } else {
      const createPayload: TenantDataSourceCreate = {
        ...basePayload,
        secret_json: secretJson ?? {},
      };
      createMutation.mutate(createPayload);
    }
  };

  const totalPages = data ? Math.ceil(data.total_count / 20) : 0;

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text fw={500}>Источники данных</Text>
        <Button leftSection={<IconPlus size={16} />} size="sm" onClick={openCreate}>
          Добавить источник
        </Button>
      </Group>
      <Alert color="blue" title="Общий принцип">
        Секреты подключения хранятся на уровне tenant отдельно от tools. Инструменты должны ссылаться на источник данных, а не хранить пароль внутри `config_json`.
      </Alert>

      {isLoading ? (
        <Center py="md"><Loader /></Center>
      ) : !data?.items.length ? (
        <Text c="dimmed" ta="center" py="md">Источники данных не настроены.</Text>
      ) : (
        <>
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Название</Table.Th>
                <Table.Th>Тип</Table.Th>
                <Table.Th>Параметры</Table.Th>
                <Table.Th>Секрет</Table.Th>
                <Table.Th>Статус</Table.Th>
                <Table.Th>Действия</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.items.map((ds) => {
                const cfg = ds.config_json || {};
                const secretMasked = ds.secret_json_masked
                  ? Object.entries(ds.secret_json_masked).map(([k, v]) => `${k}=${String(v)}`).join(', ')
                  : 'нет';
                const summary = ds.kind === 'http_api'
                  ? `${cfg.base_url || '-'}`
                  : NET_KINDS.has(ds.kind)
                    ? `${cfg.host || '-'}:${cfg.port || '-'}`
                    : `${cfg.host || '-'} / ${cfg.database || '-'}`;
                return (
                  <Table.Tr key={ds.id} style={{ cursor: 'pointer' }} onClick={() => openEdit(ds)}>
                    <Table.Td>
                      <Text size="sm" fw={500}>{ds.name}</Text>
                      {ds.description && <Text size="xs" c="dimmed">{ds.description}</Text>}
                      <Code block mt={4}>{ds.id}</Code>
                    </Table.Td>
                    <Table.Td><Badge variant="light" size="sm">{ds.kind}</Badge></Table.Td>
                    <Table.Td><Text size="sm">{summary}</Text></Table.Td>
                    <Table.Td><Text size="sm" c="dimmed">{secretMasked}</Text></Table.Td>
                    <Table.Td>
                      <Badge color={ds.is_active ? 'green' : 'gray'} size="sm">
                        {ds.is_active ? 'Активный' : 'Неактивный'}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (window.confirm(`Удалить источник данных "${ds.name}"?`)) {
                            deleteMutation.mutate(ds.id);
                          }
                        }}
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
          {totalPages > 1 && <Center><Pagination total={totalPages} value={page} onChange={setPage} /></Center>}
        </>
      )}

      <Modal
        opened={modalOpen}
        onClose={() => setModalOpen(false)}
        title={editId ? 'Редактировать источник данных' : 'Создать источник данных'}
        size="lg"
      >
        <Stack gap="md">
          <TextInput label="Название" value={name} onChange={(e) => setName(e.currentTarget.value)} required />
          <Textarea label="Описание" value={description} onChange={(e) => setDescription(e.currentTarget.value)} />
          <Select label="Тип" data={DATA_SOURCE_KIND_OPTIONS} value={kind} onChange={(v) => setKind(v || 'mariadb')} />
          {DB_KINDS.has(kind) && (
            <>
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <TextInput label="Host" value={host} onChange={(e) => setHost(e.currentTarget.value)} required />
                <NumberInput label="Port" value={port} onChange={(v) => setPort(typeof v === 'number' ? v : '')} />
                <TextInput label="Database" value={databaseName} onChange={(e) => setDatabaseName(e.currentTarget.value)} required />
                <TextInput label="Username" value={username} onChange={(e) => setUsername(e.currentTarget.value)} required />
              </SimpleGrid>
              {editId && <Switch label="Заменить пароль" checked={replaceSecret} onChange={(e) => setReplaceSecret(e.currentTarget.checked)} />}
              {(!editId || replaceSecret) && (
                <PasswordInput label="Password" value={password} onChange={(e) => setPassword(e.currentTarget.value)} required />
              )}
            </>
          )}
          {NET_KINDS.has(kind) && (
            <>
              <SimpleGrid cols={kind === 'snmp' ? 3 : 2}>
                <TextInput label="Host" placeholder="10.0.0.1" value={host} onChange={(e) => setHost(e.currentTarget.value)} required />
                <NumberInput label="Port" placeholder={kind === 'ssh' ? '22' : kind === 'telnet' ? '23' : '161'} value={port} onChange={(v) => setPort(typeof v === 'number' ? v : '')} />
                {kind === 'snmp' && (
                  <Select label="SNMP версия" data={[{ value: '2c', label: 'v2c' }, { value: '3', label: 'v3' }]} value={snmpVersion} onChange={(v) => setSnmpVersion(v || '2c')} />
                )}
              </SimpleGrid>
              {kind !== 'snmp' && (
                <TextInput label="Username" value={username} onChange={(e) => setUsername(e.currentTarget.value)} required />
              )}
              {editId && <Switch label="Заменить секрет" checked={replaceSecret} onChange={(e) => setReplaceSecret(e.currentTarget.checked)} />}
              {(!editId || replaceSecret) && (
                <>
                  {kind === 'snmp' ? (
                    <PasswordInput label="Community string" placeholder="public" value={community} onChange={(e) => setCommunity(e.currentTarget.value)} required />
                  ) : (
                    <>
                      <PasswordInput label="Password" value={password} onChange={(e) => setPassword(e.currentTarget.value)} />
                      {kind === 'ssh' && (
                        <Textarea label="Private key (PEM)" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" value={privateKey} onChange={(e) => setPrivateKey(e.currentTarget.value)} autosize minRows={2} maxRows={6} ff="monospace" styles={{ input: { fontFamily: 'monospace', fontSize: '12px' } }} />
                      )}
                    </>
                  )}
                </>
              )}
              {kind !== 'snmp' && (
                <Textarea
                  label="Команды инициализации"
                  description="Выполняются сразу после подключения, по одной на строку. Например: enable, cli, terminal length 0"
                  placeholder={"enable\nterminal length 0"}
                  value={initCommands}
                  onChange={(e) => setInitCommands(e.currentTarget.value)}
                  autosize
                  minRows={2}
                  maxRows={5}
                  ff="monospace"
                  styles={{ input: { fontFamily: 'monospace', fontSize: '13px' } }}
                />
              )}
            </>
          )}
          {kind === 'http_api' && (
            <>
              <TextInput label="Base URL" value={baseUrl} onChange={(e) => setBaseUrl(e.currentTarget.value)} placeholder="https://api.example.com" required />
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <Select
                  label="Auth type"
                  data={[
                    { value: 'none', label: 'None' },
                    { value: 'bearer', label: 'Bearer token' },
                    { value: 'header', label: 'Custom header token' },
                    { value: 'basic', label: 'Basic auth' },
                  ]}
                  value={authType}
                  onChange={(v) => setAuthType(v || 'none')}
                />
                {authType === 'header' && (
                  <TextInput label="Header name" value={authHeaderName} onChange={(e) => setAuthHeaderName(e.currentTarget.value)} />
                )}
                {authType === 'basic' && (
                  <TextInput label="Username" value={apiUsername} onChange={(e) => setApiUsername(e.currentTarget.value)} />
                )}
              </SimpleGrid>
              {editId && authType !== 'none' && (
                <Switch label="Заменить secret/token" checked={replaceSecret} onChange={(e) => setReplaceSecret(e.currentTarget.checked)} />
              )}
              {!editId || (replaceSecret && authType !== 'none') ? null : null}
              {(!editId || replaceSecret) && authType !== 'none' && (
                <PasswordInput label="Token / secret" value={apiSecret} onChange={(e) => setApiSecret(e.currentTarget.value)} required />
              )}
            </>
          )}
          <Switch label="Активный" checked={isActive} onChange={(e) => setIsActive(e.currentTarget.checked)} />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setModalOpen(false)}>Отмена</Button>
            <Button onClick={handleSave} loading={createMutation.isPending || updateMutation.isPending}>
              {editId ? 'Обновить' : 'Создать'}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
