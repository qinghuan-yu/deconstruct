const { chromium } = require('@playwright/test');
const { readFileSync, mkdirSync, writeFileSync } = require('node:fs');

(async () => {
  mkdirSync('.qa', { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const snapshot = async path => {
    await page.evaluate(async () => {
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      await Promise.all(document.getAnimations().filter(animation => animation.effect.getTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {})));
    });
    await page.screenshot({ path, animations: 'disabled' });
  };
  await page.route('**/api/settings/model', route => route.fulfill({ json: { configured: true, model: 'deepseek-chat', base_url: 'https://api.deepseek.com' } }));
  await page.goto('http://127.0.0.1:8765');
  await page.locator('#newChat').click();
  await page.locator('#promptInput').blur();
  await snapshot('.qa/desktop-empty.png');
  await page.setViewportSize({ width: 390, height: 844 });
  await snapshot('.qa/mobile-empty.png');
  await page.locator('#openSidebar').click();
  await snapshot('.qa/mobile-history.png');
  await page.setViewportSize({ width: 319, height: 671 });
  await page.locator('#modelSettings > summary').click();
  await page.locator('#apiKeyInput').click();
  await page.locator('#settingsForm').screenshot({ path: '.qa/mobile-settings-focus.png', animations: 'disabled' });
  await page.keyboard.press('Escape');
  const live = JSON.parse(readFileSync('.qa/live-result.json', 'utf8'));
  const message = live[0].message;
  const user = { id: 'question', role: 'user', status: 'complete', format_version: 2, content: '本周五天，每天 30 分钟，读完 180 页并写一页读书笔记。每天大约读 20 页，这个计划可行吗？', attachments: [] };
  await page.route('**/api/conversations', route => route.fulfill({ json: { conversations: [{ id: 'preview', title: '本周的阅读计划', updated_at: message.created_at }] } }));
  await page.route('**/api/conversations/preview', route => route.fulfill({ json: { conversation: { id: 'preview' }, messages: [user, message], cursor: 42 } }));
  await page.addInitScript(() => {
    window.EventSource = class {
      constructor() { setTimeout(() => this.onopen?.(), 0); }
      addEventListener() {}
      close() {}
    };
  });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.reload();
  await page.locator('.final-answer h1').waitFor();
  await page.locator('#chat').evaluate(element => element.scrollTo(0, 0));
  await snapshot('.qa/desktop-answer.png');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#chat').evaluate(element => element.scrollTo(0, 0));
  await snapshot('.qa/mobile-answer.png');
  writeFileSync('.qa/browser-errors.json', JSON.stringify(errors), 'utf8');
  console.log(JSON.stringify({ screenshots: 6, errors }));
  await browser.close();
})();
