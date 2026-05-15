import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backendTarget = process.env.VITE_BACKEND_TARGET || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) {
            return undefined;
          }

          if (
            id.includes('@mantine/core') ||
            id.includes('@mantine/hooks') ||
            id.includes('@mantine/dates') ||
            id.includes('@mantine/notifications') ||
            id.includes('@mantine/charts') ||
            id.includes('@mantine/code-highlight') ||
            id.includes('@emotion/')
          ) {
            return 'mantine';
          }

          if (
            id.includes('react-router') ||
            id.includes('@tanstack/react-query') ||
            id.includes('axios') ||
            id.includes('dayjs')
          ) {
            return 'app-vendor';
          }

          if (id.includes('recharts') || id.includes('d3-') || id.includes('victory-vendor')) {
            return 'charts';
          }

          return 'vendor';
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: backendTarget,
        changeOrigin: true,
      },
      '/health': {
        target: backendTarget,
        changeOrigin: true,
      },
    },
  },
});
