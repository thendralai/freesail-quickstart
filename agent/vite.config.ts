import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    target: 'node18',
    outDir: 'dist',
    sourcemap: true,
    lib: {
      entry: 'src/index.ts',
      formats: ['es'],
      fileName: 'index'
    },
    rollupOptions: {
      external: [
        'express',
        'cors',
        'zod',
        '@modelcontextprotocol/sdk',
        '@modelcontextprotocol/sdk/client/index.js',
        '@modelcontextprotocol/sdk/client/stdio.js',
        '@modelcontextprotocol/sdk/types.js',
        '@langchain/core',
        '@langchain/google-genai',
        /^node:/,
        'path',
        'url',
        'fs',
        'http',
        'https',
        'events',
        'child_process',
        'stream',
        'crypto'
      ]
    }
  }
});
