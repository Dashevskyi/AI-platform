import {
  SimpleGrid,
  Card,
  Text,
  Title,
  Group,
  Badge,
  Button,
  Stack,
  Loader,
  Alert,
} from '@mantine/core';
import {
  IconBuilding,
  IconHeartbeat,
  IconPlus,
  IconAlertCircle,
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { tenantsApi, healthApi } from '../shared/api/endpoints';

export function DashboardPage() {
  const navigate = useNavigate();

  const {
    data: tenantsData,
    isLoading: tenantsLoading,
    error: tenantsError,
  } = useQuery({
    queryKey: ['tenants', 'list', 1],
    queryFn: () => tenantsApi.list(1, 1),
  });

  const {
    data: healthData,
    isLoading: healthLoading,
    error: healthError,
  } = useQuery({
    queryKey: ['health'],
    queryFn: healthApi.check,
    refetchInterval: 30_000,
  });

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="center">
        <Title order={2}>Панель управления</Title>
        <Button
          leftSection={<IconPlus size={16} />}
          onClick={() => navigate('/tenants?create=1')}
        >
          Новый тенант
        </Button>
      </Group>

      {(tenantsError || healthError) && (
        <Alert icon={<IconAlertCircle size={16} />} color="red" variant="light">
          Не удалось загрузить некоторые данные. Сервер может быть недоступен.
        </Alert>
      )}

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
        <Card shadow="sm" padding="lg" radius="md" withBorder>
          <Group justify="space-between" mb="md">
            <Text fw={500} size="lg">
              Тенанты
            </Text>
            <IconBuilding size={24} color="var(--mantine-color-blue-6)" />
          </Group>
          {tenantsLoading ? (
            <Loader size="sm" />
          ) : (
            <Text size="xl" fw={700}>
              {tenantsData?.total_count ?? 0}
            </Text>
          )}
          <Text size="sm" c="dimmed" mt="xs">
            Всего зарегистрированных тенантов
          </Text>
          <Button
            variant="light"
            fullWidth
            mt="md"
            onClick={() => navigate('/tenants')}
          >
            Показать все
          </Button>
        </Card>

        <Card shadow="sm" padding="lg" radius="md" withBorder>
          <Group justify="space-between" mb="md">
            <Text fw={500} size="lg">
              Здоровье системы
            </Text>
            <IconHeartbeat size={24} color="var(--mantine-color-green-6)" />
          </Group>
          {healthLoading ? (
            <Loader size="sm" />
          ) : healthData ? (
            <Stack gap="xs">
              <Group>
                <Text size="sm">Статус:</Text>
                <Badge
                  color={healthData.status === 'healthy' ? 'green' : 'red'}
                >
                  {healthData.status}
                </Badge>
              </Group>
              <Group>
                <Text size="sm">База данных:</Text>
                <Badge
                  color={healthData.database === 'connected' ? 'green' : 'red'}
                >
                  {healthData.database}
                </Badge>
              </Group>
              {healthData.ollama && (
                <Group>
                  <Text size="sm">Ollama:</Text>
                  <Badge color={healthData.ollama === 'ok' ? 'green' : 'yellow'}>
                    {healthData.ollama}
                  </Badge>
                </Group>
              )}
            </Stack>
          ) : (
            <Badge color="red">Недоступно</Badge>
          )}
        </Card>

        <Card shadow="sm" padding="lg" radius="md" withBorder>
          <Group justify="space-between" mb="md">
            <Text fw={500} size="lg">
              Быстрые действия
            </Text>
          </Group>
          <Stack gap="sm">
            <Button
              variant="light"
              fullWidth
              onClick={() => navigate('/tenants?create=1')}
              leftSection={<IconPlus size={16} />}
            >
              Создать тенант
            </Button>
            <Button
              variant="light"
              fullWidth
              color="gray"
              onClick={() => navigate('/tenants')}
            >
              Управление тенантами
            </Button>
          </Stack>
        </Card>
      </SimpleGrid>
    </Stack>
  );
}
