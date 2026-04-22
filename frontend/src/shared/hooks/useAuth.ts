import { useCallback, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { authApi } from '../api/endpoints';
import type { LoginRequest } from '../api/types';

export function useAuth() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const token = localStorage.getItem('auth_token');
  const isAuthenticated = !!token;

  const {
    data: user,
    isLoading: userLoading,
    error: userError,
  } = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: authApi.me,
    enabled: isAuthenticated,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  const login = useCallback(
    async (credentials: LoginRequest) => {
      const response = await authApi.login(credentials);
      localStorage.setItem('auth_token', response.access_token);
      await queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
      navigate('/dashboard');
    },
    [queryClient, navigate]
  );

  const logout = useCallback(() => {
    authApi.logout();
    queryClient.clear();
    navigate('/login');
  }, [queryClient, navigate]);

  return useMemo(
    () => ({
      user,
      userLoading,
      userError,
      isAuthenticated,
      login,
      logout,
    }),
    [user, userLoading, userError, isAuthenticated, login, logout]
  );
}
