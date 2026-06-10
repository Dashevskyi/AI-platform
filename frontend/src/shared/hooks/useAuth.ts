import { useCallback, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { authApi } from '../api/endpoints';
import type { LoginRequest } from '../api/types';

// The access token now lives in an HttpOnly cookie (not readable by JS), so we
// can't synchronously tell "logged in?" from it. We keep a non-secret boolean
// marker in localStorage purely to gate the /me probe and the route guards; the
// real authority is the cookie, validated server-side on every request.
const AUTH_FLAG = 'auth_active';
export const isAuthFlagSet = () => localStorage.getItem(AUTH_FLAG) === '1';

export function useAuth() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const isAuthenticated = isAuthFlagSet();

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
      await authApi.login(credentials); // sets the HttpOnly cookie via Set-Cookie
      localStorage.setItem(AUTH_FLAG, '1');
      localStorage.removeItem('auth_token'); // drop any legacy token from older builds
      await queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
      const me = await authApi.me();
      queryClient.setQueryData(['auth', 'me'], me);
      if (me.role === 'tenant_admin' && me.tenant_id) {
        navigate(`/tenants/${me.tenant_id}`);
      } else {
        navigate('/dashboard');
      }
    },
    [queryClient, navigate]
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout(); // revokes the token server-side + clears cookie
    } catch {
      /* best-effort — clear local state regardless */
    }
    localStorage.removeItem(AUTH_FLAG);
    localStorage.removeItem('auth_token');
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
