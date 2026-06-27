import { useState, useMemo, type ReactNode } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  AppShell as MantineAppShell,
  Burger,
  Button,
  Group,
  Modal,
  NavLink,
  PasswordInput,
  Stack,
  Title,
  ActionIcon,
  Menu,
  Text,
  Divider,
  Skeleton,
  Select,
  Tooltip,
  Box,
  useMantineColorScheme,
  Avatar,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import {
  IconDashboard,
  IconBuilding,
  IconKey,
  IconSun,
  IconMoon,
  IconLogout,
  IconUser,
  IconBook,
  IconMessage,
  IconPlus,
  IconRobot,
  IconCpu,
  IconCalculator,
  IconVolume,
  IconArrowLeft,
  IconChevronRight,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../hooks/useAuth';
import { usePermissions } from '../hooks/usePermissions';
import { authApi, chatsApi, tenantsApi, assistantsApi } from '../api/endpoints';
import { SIDEBAR_COLLAPSED_W, SIDEBAR_EXPANDED_W } from './CollapsibleIconNav';
import { SecondaryNavProvider, useSecondaryNavContext } from './SecondaryNavContext';

function SidebarNavLink({
  label,
  description,
  icon,
  active,
  onClick,
  expanded,
  dirty,
  rightSection,
}: {
  label: string;
  description?: string;
  icon: ReactNode;
  active?: boolean;
  onClick: () => void;
  expanded: boolean;
  dirty?: boolean;
  rightSection?: ReactNode;
}) {
  return (
    <Tooltip
      label={
        description ? (
          <div>
            <Text size="xs" fw={600}>{label}</Text>
            <Text size="xs" c="dimmed">{description}</Text>
          </div>
        ) : label
      }
      position="right"
      withArrow
      disabled={expanded}
      multiline
      w={220}
    >
      <NavLink
        label={expanded ? label : undefined}
        description={expanded ? description : undefined}
        leftSection={
          <Box style={{ width: 28, display: 'flex', justifyContent: 'center', flexShrink: 0 }}>
            {icon}
          </Box>
        }
        rightSection={
          expanded
            ? (dirty
              ? <Box w={6} h={6} style={{ borderRadius: '50%', background: 'var(--mantine-color-yellow-6)' }} />
              : rightSection)
            : dirty
              ? <Box w={6} h={6} style={{ borderRadius: '50%', background: 'var(--mantine-color-yellow-6)' }} />
              : null
        }
        active={active}
        onClick={onClick}
        variant="filled"
        mb={4}
        styles={{
          root: { paddingLeft: expanded ? undefined : 8, paddingRight: expanded ? undefined : 8 },
          label: { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
        }}
      />
    </Tooltip>
  );
}

function NavbarBody({
  navItems,
  tenantId,
  canSeeChats,
  assistantsData,
  assistantFilter,
  setAssistantFilter,
  chatsLoading,
  chatsData,
  activeChatId,
  handleCreateChat,
  navigate,
  setOpened,
  location,
  expanded,
}: {
  navItems: { label: string; icon: typeof IconDashboard; path: string }[];
  tenantId: string | null;
  canSeeChats: boolean;
  assistantsData: Awaited<ReturnType<typeof assistantsApi.list>> | undefined;
  assistantFilter: string | null;
  setAssistantFilter: (v: string | null) => void;
  chatsLoading: boolean;
  chatsData: Awaited<ReturnType<typeof chatsApi.list>> | undefined;
  activeChatId: string | null;
  handleCreateChat: () => void;
  navigate: ReturnType<typeof useNavigate>;
  setOpened: (v: boolean | ((o: boolean) => boolean)) => void;
  location: ReturnType<typeof useLocation>;
  expanded: boolean;
}) {
  const secondaryNav = useSecondaryNavContext()?.config;

  const go = (path: string) => {
    navigate(path);
    setOpened(false);
  };

  return (
    <>
      {navItems.map((item) => (
        <SidebarNavLink
          key={item.path}
          label={item.label}
          icon={<item.icon size={20} stroke={1.5} />}
          active={location.pathname.startsWith(item.path)}
          onClick={() => go(item.path)}
          expanded={expanded}
        />
      ))}

      {secondaryNav && (
        <>
          <Divider my="sm" />
          {expanded && (
            <Text size="xs" c="dimmed" tt="uppercase" fw={600} px={4} py={4} truncate>
              {secondaryNav.title}
            </Text>
          )}
          <Stack gap={2}>
            {secondaryNav.items.map((item) => (
              <SidebarNavLink
                key={item.id}
                label={item.label}
                description={item.description}
                icon={item.icon}
                active={item.active}
                onClick={item.onClick}
                expanded={expanded}
                dirty={item.dirty}
                rightSection={item.rightSection}
              />
            ))}
          </Stack>
          {secondaryNav.footer && expanded && (
            <Box mt="xs">{secondaryNav.footer}</Box>
          )}
        </>
      )}

      {tenantId && canSeeChats && !secondaryNav && (
        <>
          <Divider my="sm" />
          {expanded ? (
            <Group justify="space-between" mb={8} px={4}>
              <Text size="sm" fw={600} c="dimmed">Чаты</Text>
              <ActionIcon variant="light" size="sm" onClick={handleCreateChat} aria-label="Новый чат">
                <IconPlus size={14} />
              </ActionIcon>
            </Group>
          ) : (
            <Tooltip label="Новый чат" position="right" withArrow>
              <ActionIcon variant="light" size="md" mb={6} onClick={handleCreateChat} aria-label="Новый чат" mx="auto" display="block">
                <IconPlus size={16} />
              </ActionIcon>
            </Tooltip>
          )}
          {expanded && assistantsData && assistantsData.length > 1 && (
            <Select
              size="xs" mb={8} px={4}
              label="Ассистент"
              placeholder="Все ассистенты"
              data={assistantsData.map((a) => ({
                value: a.id, label: a.name + (a.is_default ? ' (по умолчанию)' : ''),
              }))}
              value={assistantFilter}
              onChange={setAssistantFilter}
              clearable
            />
          )}
          {chatsLoading ? (
            <>
              <Skeleton height={32} mb={4} />
              <Skeleton height={32} mb={4} />
              <Skeleton height={32} mb={4} />
            </>
          ) : (
            chatsData?.items?.map((chat) => {
              const title = chat.title || `Чат ${chat.id.slice(0, 8)}...`;
              return (
                <SidebarNavLink
                  key={chat.id}
                  label={title}
                  icon={<IconMessage size={16} stroke={1.5} />}
                  active={chat.id === activeChatId}
                  onClick={() => go(`/tenants/${tenantId}/chat/${chat.id}`)}
                  expanded={expanded}
                />
              );
            })
          )}
        </>
      )}
    </>
  );
}

export function AppShellLayout() {
  const [opened, setOpened] = useState(false);
  const [sidebarHovered, setSidebarHovered] = useState(false);
  const isMobile = useMediaQuery('(max-width: 47.99em)');
  const sidebarExpanded = isMobile ? opened : sidebarHovered;
  const sidebarWidth = sidebarExpanded ? SIDEBAR_EXPANDED_W : SIDEBAR_COLLAPSED_W;

  const { colorScheme, toggleColorScheme } = useMantineColorScheme();
  const { user, logout } = useAuth();
  const { isSuperadmin, isTenantAdmin, tenantId: myTenantId, has: hasPerm } = usePermissions();
  const navigate = useNavigate();
  const location = useLocation();
  const [pwdOpen, setPwdOpen] = useState(false);
  const [curPwd, setCurPwd] = useState('');
  const [newPwd, setNewPwd] = useState('');
  const [newPwd2, setNewPwd2] = useState('');

  const changePwdMut = useMutation({
    mutationFn: () => authApi.changePassword({ current_password: curPwd, new_password: newPwd }),
    onSuccess: () => {
      notifications.show({ title: 'Готово', message: 'Пароль изменён', color: 'green' });
      setPwdOpen(false);
      setCurPwd('');
      setNewPwd('');
      setNewPwd2('');
    },
    onError: (e: Error & { response?: { data?: { detail?: string } } }) => {
      notifications.show({
        title: 'Ошибка',
        message: e.response?.data?.detail || e.message || 'Не удалось изменить пароль',
        color: 'red',
      });
    },
  });

  const handleChangePassword = () => {
    if (!curPwd || !newPwd) {
      notifications.show({ title: 'Ошибка', message: 'Заполните все поля', color: 'red' });
      return;
    }
    if (newPwd.length < 4) {
      notifications.show({ title: 'Ошибка', message: 'Новый пароль слишком короткий (минимум 4 символа)', color: 'red' });
      return;
    }
    if (newPwd !== newPwd2) {
      notifications.show({ title: 'Ошибка', message: 'Пароли не совпадают', color: 'red' });
      return;
    }
    if (curPwd === newPwd) {
      notifications.show({ title: 'Ошибка', message: 'Новый пароль совпадает с текущим', color: 'red' });
      return;
    }
    changePwdMut.mutate();
  };

  const navItems = isSuperadmin
    ? [
        { label: 'Панель управления', icon: IconDashboard, path: '/dashboard' },
        { label: 'Каталог моделей', icon: IconRobot, path: '/models' },
        { label: 'Тенанты', icon: IconBuilding, path: '/tenants' },
        { label: 'Инфраструктура', icon: IconCpu, path: '/infrastructure' },
        { label: 'Калькулятор', icon: IconCalculator, path: '/calculator' },
        { label: 'Локальный TTS', icon: IconVolume, path: '/tts-local' },
      ]
    : isTenantAdmin && myTenantId
      ? [
          { label: 'Мой тенант', icon: IconBuilding, path: `/tenants/${myTenantId}` },
        ]
      : [];

  const tenantId = useMemo(() => {
    const match = location.pathname.match(/^\/tenants\/([^/]+)/);
    return match ? match[1] : null;
  }, [location.pathname]);

  const activeChatId = useMemo(() => {
    const match = location.pathname.match(/^\/tenants\/[^/]+\/chat\/([^/]+)/);
    return match ? match[1] : null;
  }, [location.pathname]);

  const queryClient = useQueryClient();

  const { data: tenant } = useQuery({
    queryKey: ['tenants', tenantId],
    queryFn: () => tenantsApi.get(tenantId!),
    enabled: !!tenantId,
  });

  const canSeeChats = hasPerm('chats');

  const { data: assistantsData } = useQuery({
    queryKey: ['tenants', tenantId, 'assistants'],
    queryFn: () => assistantsApi.list(tenantId!),
    enabled: !!tenantId && canSeeChats && (isSuperadmin || isTenantAdmin),
  });

  const [assistantFilter, setAssistantFilter] = useState<string | null>(null);

  const { data: chatsData, isLoading: chatsLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'chats', 'list', assistantFilter],
    queryFn: () => chatsApi.list(tenantId!, 1, 10, assistantFilter),
    enabled: !!tenantId && canSeeChats,
  });

  const handleCreateChat = async () => {
    if (!tenantId) return;
    const chat = await chatsApi.create(
      tenantId,
      assistantFilter ? { assistant_id: assistantFilter } : {},
    );
    queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'chats', 'list'] });
    navigate(`/tenants/${tenantId}/chat/${chat.id}`);
    setOpened(false);
  };

  return (
    <SecondaryNavProvider>
      <MantineAppShell
        header={{ height: 60 }}
        navbar={{
          width: sidebarWidth,
          breakpoint: 'sm',
          collapsed: { mobile: !opened },
        }}
        padding="md"
        styles={{
          navbar: {
            transition: 'width 180ms ease',
            overflow: 'hidden',
          },
          main: {
            transition: 'padding-left 180ms ease',
          },
        }}
      >
        <MantineAppShell.Header>
          <Group h="100%" px="md" justify="space-between">
            <Group gap="xs">
              <Burger
                opened={opened}
                onClick={() => setOpened((o) => !o)}
                hiddenFrom="sm"
                size="sm"
              />
              <Title
                order={3}
                style={{ cursor: 'pointer' }}
                onClick={() => navigate('/dashboard')}
              >
                AI Platform
              </Title>
              {tenant && tenantId && (
                <>
                  <IconChevronRight size={16} color="var(--mantine-color-dimmed)" />
                  <ActionIcon
                    variant="subtle"
                    size="sm"
                    onClick={() => navigate(`/tenants/${tenantId}`)}
                    aria-label="К настройкам тенанта"
                  >
                    <IconArrowLeft size={16} />
                  </ActionIcon>
                  <Text
                    size="sm"
                    fw={500}
                    c="dimmed"
                    style={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/tenants/${tenantId}`)}
                  >
                    {tenant.name}
                  </Text>
                </>
              )}
            </Group>
            <Group>
              <ActionIcon
                variant="default"
                size="lg"
                component="a"
                href="/docs"
                target="_blank"
                aria-label="Документация API"
              >
                <IconBook size={18} />
              </ActionIcon>
              <ActionIcon
                variant="default"
                size="lg"
                onClick={() => toggleColorScheme()}
                aria-label="Переключить тему"
              >
                {colorScheme === 'dark' ? <IconSun size={18} /> : <IconMoon size={18} />}
              </ActionIcon>
              <Menu shadow="md" width={200}>
                <Menu.Target>
                  <ActionIcon variant="default" size="lg" aria-label="Меню пользователя">
                    <Avatar size="sm" radius="xl" color="blue">
                      {user?.login?.charAt(0).toUpperCase() || 'U'}
                    </Avatar>
                  </ActionIcon>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Label>
                    <Text size="sm" fw={500}>
                      {user?.login || 'Пользователь'}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {user?.role || 'admin'}
                    </Text>
                  </Menu.Label>
                  <Menu.Divider />
                  <Menu.Item leftSection={<IconUser size={14} />} disabled>
                    Профиль
                  </Menu.Item>
                  <Menu.Item
                    leftSection={<IconKey size={14} />}
                    onClick={() => setPwdOpen(true)}
                  >
                    Сменить пароль
                  </Menu.Item>
                  <Menu.Item
                    leftSection={<IconLogout size={14} />}
                    color="red"
                    onClick={logout}
                  >
                    Выход
                  </Menu.Item>
                </Menu.Dropdown>
              </Menu>
            </Group>
          </Group>
        </MantineAppShell.Header>

        <MantineAppShell.Navbar
          p={sidebarExpanded ? 'md' : 'xs'}
          onMouseEnter={() => { if (!isMobile) setSidebarHovered(true); }}
          onMouseLeave={() => { if (!isMobile) setSidebarHovered(false); }}
        >
          <NavbarBody
            navItems={navItems}
            tenantId={tenantId}
            canSeeChats={canSeeChats}
            assistantsData={assistantsData}
            assistantFilter={assistantFilter}
            setAssistantFilter={setAssistantFilter}
            chatsLoading={chatsLoading}
            chatsData={chatsData}
            activeChatId={activeChatId}
            handleCreateChat={handleCreateChat}
            navigate={navigate}
            setOpened={setOpened}
            location={location}
            expanded={sidebarExpanded}
          />
        </MantineAppShell.Navbar>

        <MantineAppShell.Main>
          <Outlet />
        </MantineAppShell.Main>

        <Modal
          opened={pwdOpen}
          onClose={() => setPwdOpen(false)}
          title="Смена пароля"
          size="sm"
        >
          <Stack gap="sm">
            <PasswordInput
              label="Текущий пароль"
              value={curPwd}
              onChange={(e) => setCurPwd(e.currentTarget.value)}
              autoComplete="current-password"
              required
            />
            <PasswordInput
              label="Новый пароль"
              value={newPwd}
              onChange={(e) => setNewPwd(e.currentTarget.value)}
              autoComplete="new-password"
              description="Минимум 4 символа"
              required
            />
            <PasswordInput
              label="Повтор нового пароля"
              value={newPwd2}
              onChange={(e) => setNewPwd2(e.currentTarget.value)}
              autoComplete="new-password"
              error={newPwd2 && newPwd !== newPwd2 ? 'Пароли не совпадают' : undefined}
              required
            />
            <Group justify="flex-end" gap="xs" mt="xs">
              <Button variant="default" onClick={() => setPwdOpen(false)}>Отмена</Button>
              <Button onClick={handleChangePassword} loading={changePwdMut.isPending}>
                Сменить
              </Button>
            </Group>
          </Stack>
        </Modal>
      </MantineAppShell>
    </SecondaryNavProvider>
  );
}
