import axios from 'axios';
import { notifications } from '@mantine/notifications';

const apiClient = axios.create({
  baseURL: '',
});

// Auth travels in an HttpOnly cookie (sent automatically on same-origin
// requests), so there is no Authorization header to attach here.

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('auth_active');
      localStorage.removeItem('auth_token');
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }
    if (error.response?.status === 403) {
      notifications.show({
        id: 'api-forbidden',
        title: 'Доступ запрещён',
        message: error.response?.data?.detail || 'У вас недостаточно прав для этого действия.',
        color: 'red',
      });
    }
    if (error.response?.status === 429) {
      notifications.show({
        id: 'api-rate-limited',
        title: 'Слишком много запросов',
        message: error.response?.data?.detail || 'Попробуйте ещё раз через минуту.',
        color: 'orange',
      });
    }
    return Promise.reject(error);
  }
);

export default apiClient;
