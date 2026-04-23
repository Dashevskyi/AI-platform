import { useState, useMemo } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import {
  AppShell as MantineAppShell,
  Burger,
  Group,
  NavLink,
  Title,
  ActionIcon,
  Menu,
  Text,
  Divider,
  Skeleton,
  useMantineColorScheme,
  Avatar,
} from '@mantine/core';
import {
  IconDashboard,
  IconBuilding,
  IconSun,
  IconMoon,
  IconLogout,
  IconUser,
  IconBook,
  IconMessage,
  IconPlus,
  IconRobot,
  IconArrowLeft,
  IconChevronRight,
} from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../hooks/useAuth';
import { chatsApi, tenantsApi } from '../api/endpoints';

export function AppShellLayout() {
  const [opened, setOpened] = useState(false);
  const { colorScheme, toggleColorScheme } = useMantineColorScheme();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const navItems = [
    { label: 'Панель управления', icon: IconDashboard, path: '/dashboard' },
    { label: 'Каталог моделей', icon: IconRobot, path: '/models' },
    { label: 'Тенанты', icon: IconBuilding, path: '/tenants' },
  ];

  // Extract tenant ID from URL if on a tenant page
  const tenantId = useMemo(() => {
    const match = location.pathname.match(/^\/tenants\/([^/]+)/);
    return match ? match[1] : null;
  }, [location.pathname]);

  // Extract active chat ID from URL
  const activeChatId = useMemo(() => {
    const match = location.pathname.match(/^\/tenants\/[^/]+\/chat\/([^/]+)/);
    return match ? match[1] : null;
  }, [location.pathname]);

  const queryClient = useQueryClient();

  // Load tenant info when on tenant page
  const { data: tenant } = useQuery({
    queryKey: ['tenants', tenantId],
    queryFn: () => tenantsApi.get(tenantId!),
    enabled: !!tenantId,
  });

  const { data: chatsData, isLoading: chatsLoading } = useQuery({
    queryKey: ['tenants', tenantId, 'chats', 'list'],
    queryFn: () => chatsApi.list(tenantId!, 1, 10),
    enabled: !!tenantId,
  });

  const handleCreateChat = async () => {
    if (!tenantId) return;
    const chat = await chatsApi.create(tenantId, {});
    queryClient.invalidateQueries({ queryKey: ['tenants', tenantId, 'chats', 'list'] });
    navigate(`/tenants/${tenantId}/chat/${chat.id}`);
    setOpened(false);
  };

  return (
    <MantineAppShell
      header={{ height: 60 }}
      navbar={{
        width: 250,
        breakpoint: 'sm',
        collapsed: { mobile: !opened },
      }}
      padding="md"
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

      <MantineAppShell.Navbar p="md">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            label={item.label}
            leftSection={<item.icon size={20} />}
            active={location.pathname.startsWith(item.path)}
            onClick={() => {
              navigate(item.path);
              setOpened(false);
            }}
            variant="filled"
            mb={4}
          />
        ))}

        {tenantId && (
          <>
            <Divider my="md" />
            <Group justify="space-between" mb={8} px={4}>
              <Text size="sm" fw={600} c="dimmed">Чаты</Text>
              <ActionIcon variant="light" size="sm" onClick={handleCreateChat} aria-label="Новый чат">
                <IconPlus size={14} />
              </ActionIcon>
            </Group>
            {chatsLoading ? (
              <>
                <Skeleton height={32} mb={4} />
                <Skeleton height={32} mb={4} />
                <Skeleton height={32} mb={4} />
              </>
            ) : (
              chatsData?.items?.map((chat) => (
                <NavLink
                  key={chat.id}
                  label={chat.title || `Чат ${chat.id.slice(0, 8)}...`}
                  leftSection={<IconMessage size={16} />}
                  active={chat.id === activeChatId}
                  onClick={() => {
                    navigate(`/tenants/${tenantId}/chat/${chat.id}`);
                    setOpened(false);
                  }}
                  variant="filled"
                  mb={2}
                  style={{ fontSize: 13 }}
                />
              ))
            )}
          </>
        )}
      </MantineAppShell.Navbar>

      <MantineAppShell.Main>
        <Outlet />
      </MantineAppShell.Main>
    </MantineAppShell>
  );
}
