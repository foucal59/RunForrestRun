import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // recharts (+ ses dépendances d3) est lourd et n'est utilisé que par
        // les pages à graphes, chargées à la demande. On le regroupe dans un
        // chunk dédié : hors du bundle initial, et mis en cache une seule fois
        // puis partagé entre toutes les pages qui l'importent.
        manualChunks(id) {
          if (id.includes('node_modules/recharts') || id.includes('node_modules/d3')) {
            return 'charts'
          }
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8080'
    }
  }
})
