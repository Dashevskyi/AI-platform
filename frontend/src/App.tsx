import { Routes, Route, Navigate } from 'react-router-dom';
import { AppShellLayout } from './shared/ui/AppShell';
import { LoginPage } from './pages/LoginPage';
import { DashboardPage } from './pages/DashboardPage';
import { TenantListPage } from './pages/TenantListPage';
import { TenantDetailPage } from './pages/TenantDetailPage';
import { ModelsPage } from './pages/ModelsPage';
import { ChatPage } from './pages/ChatPage';

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('auth_token');
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}

function RootRedirect() {
  const token = localStorage.getItem('auth_token');
  return <Navigate to={token ? '/dashboard' : '/login'} replace />;
}

export default function App() {
  return (
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
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/models" element={<ModelsPage />} />
        <Route path="/tenants" element={<TenantListPage />} />
        <Route path="/tenants/:id" element={<TenantDetailPage />} />
        <Route path="/tenants/:id/chat" element={<ChatPage />} />
        <Route path="/tenants/:id/chat/:chatId" element={<ChatPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
