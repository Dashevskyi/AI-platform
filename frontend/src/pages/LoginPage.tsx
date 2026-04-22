import { useState } from 'react';
import {
  Card,
  TextInput,
  PasswordInput,
  Button,
  Title,
  Text,
  Stack,
  Center,
  Alert,
  Box,
} from '@mantine/core';
import { IconAlertCircle } from '@tabler/icons-react';
import { useAuth } from '../shared/hooks/useAuth';
import { Navigate } from 'react-router-dom';

export function LoginPage() {
  const { login, isAuthenticated } = useAuth();
  const [loginValue, setLoginValue] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login({ login: loginValue, password });
    } catch (err: unknown) {
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Ошибка входа. Проверьте ваши учётные данные.';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Center h="100vh" bg="var(--mantine-color-body)">
      <Box w={420}>
        <Card shadow="md" padding="xl" radius="md" withBorder>
          <form onSubmit={handleSubmit}>
            <Stack gap="md">
              <Box ta="center">
                <Title order={2} mb={4}>
                  AI Platform
                </Title>
                <Text c="dimmed" size="sm">
                  Войти в панель администратора
                </Text>
              </Box>

              {error && (
                <Alert
                  icon={<IconAlertCircle size={16} />}
                  color="red"
                  variant="light"
                  onClose={() => setError('')}
                  withCloseButton
                >
                  {error}
                </Alert>
              )}

              <TextInput
                label="Логин"
                placeholder="admin"
                value={loginValue}
                onChange={(e) => setLoginValue(e.currentTarget.value)}
                required
                autoFocus
              />

              <PasswordInput
                label="Пароль"
                placeholder="Введите пароль"
                value={password}
                onChange={(e) => setPassword(e.currentTarget.value)}
                required
              />

              <Button type="submit" fullWidth loading={loading} mt="sm">
                Войти
              </Button>
            </Stack>
          </form>
        </Card>
      </Box>
    </Center>
  );
}
