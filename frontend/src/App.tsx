import { Suspense, lazy } from 'react';
import { Center, Loader } from '@mantine/core';
import { Routes, Route, Navigate } from 'react-router-dom';
import { AppShellLayout } from './shared/ui/AppShell';
import { useAuth } from './shared/hooks/useAuth';
import { usePermissions } from './shared/hooks/usePermissions';

const LoginPage = lazy(() =>
  import('./pages/LoginPage').then((module) => ({ default: module.LoginPage }))
);
const DashboardPage = lazy(() =>
  import('./pages/DashboardPage').then((module) => ({ default: module.DashboardPage }))
);
const TenantListPage = lazy(() =>
  import('./pages/TenantListPage').then((module) => ({ default: module.TenantListPage }))
);
const TenantDetailPage = lazy(() =>
  import('./pages/TenantDetailPage').then((module) => ({ default: module.TenantDetailPage }))
);
const ModelsPage = lazy(() =>
  import('./pages/ModelsPage').then((module) => ({ default: module.ModelsPage }))
);
const ChatPage = lazy(() =>
  import('./pages/ChatPage').then((module) => ({ default: module.ChatPage }))
);
const InfrastructurePage = lazy(() =>
  import('./pages/InfrastructurePage').then((module) => ({ default: module.InfrastructurePage }))
);

function RouteFallback() {
  return (
    <Center py="xl">
      <Loader />
    </Center>
  );
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('auth_token');
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

function SuperadminOnly({ children }: { children: React.ReactNode }) {
  const { user, userLoading } = useAuth();
  const { isTenantAdmin, tenantId } = usePermissions();
  if (userLoading || !user) return <RouteFallback />;
  if (isTenantAdmin && tenantId) {
    return <Navigate to={`/tenants/${tenantId}`} replace />;
  }
  return <>{children}</>;
}

function RootRedirect() {
  const token = localStorage.getItem('auth_token');
  return <Navigate to={token ? '/dashboard' : '/login'} replace />;
}

export default function App() {
  return (
    <Suspense fallback={<RouteFallback />}>
      <Routes>
        <Route path="/" element={<RootRedirect />} />
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <ProtectedRoute>
              <AppShellLayout />
            </ProtectedRoute>
          }
        >
          <Route path="/dashboard" element={<SuperadminOnly><DashboardPage /></SuperadminOnly>} />
          <Route path="/models" element={<SuperadminOnly><ModelsPage /></SuperadminOnly>} />
          <Route path="/infrastructure" element={<SuperadminOnly><InfrastructurePage /></SuperadminOnly>} />
          <Route path="/tenants" element={<SuperadminOnly><TenantListPage /></SuperadminOnly>} />
          <Route path="/tenants/:id" element={<TenantDetailPage />} />
          <Route path="/tenants/:id/chat" element={<ChatPage />} />
          <Route path="/tenants/:id/chat/:chatId" element={<ChatPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
