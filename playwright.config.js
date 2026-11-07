const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests/browser',
  workers: 1,
  timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:8765', channel: 'msedge', viewport: { width: 1440, height: 960 } },
  reporter: 'list',
});
