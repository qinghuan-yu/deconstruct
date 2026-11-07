const { test, expect } = require('@playwright/test');

async function setupNavigation(page, { shortIds = [] } = {}) {
  const conversations = Array.from({ length: 18 }, (_, index) => ({
    id: `c${index}`, title: `对话 ${index + 1}`, updated_at: 1794052800,
  }));
  const snapshot = id => ({
    conversation: { id }, cursor: 42,
    messages: [{ id: `answer-${id}`, role: 'assistant', mode: 'quick', status: 'complete',
      content: `${id} 的回答。\n\n${'供测试阅读位置的段落。\n\n'.repeat(shortIds.includes(id) ? 1 : 70)}`, steps: {} }],
  });
  const gates = new Map(), requests = [], failures = new Set();
  await page.addInitScript(() => {
    window.__streams = [];
    window.EventSource = class {
      constructor(url) { this.url = url; this.listeners = {}; window.__streams.push(this); }
      addEventListener(name, fn) { this.listeners[name] = fn; }
      close() { this.closed = true; }
      emit(type, payload) { this.listeners[type]?.({ data: JSON.stringify({ type, payload }) }); }
    };
  });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/settings/model') return route.fulfill({ json: { configured: true, model: 'deepseek-chat' } });
    if (path === '/api/conversations') return route.fulfill({ json: { conversations } });
    const id = path.split('/').at(-1);
    requests.push(id);
    if (gates.has(id)) await gates.get(id);
    if (failures.has(id)) return route.fulfill({ status: 503, json: { detail: '暂时无法加载对话' } });
    return route.fulfill({ json: snapshot(id) });
  });
  await page.goto('/');
  await expect(page.locator('.message')).toHaveAttribute('data-message-id', 'answer-c0');
  await page.waitForTimeout(250);
  return {
    requests, failures, snapshot,
    hold(id) {
      let release;
      gates.set(id, new Promise(resolve => { release = resolve; }));
      return () => { gates.delete(id); release(); };
    },
  };
}

test('switch keeps the current page stable while loading and restores each reading position', async ({ page }) => {
  const control = await setupNavigation(page);
  await page.locator('#chat').evaluate(element => element.scrollTo(0, 1200));
  const release = control.hold('c1');
  await page.getByRole('button', { name: '对话 2', exact: true }).click();
  await expect.poll(() => control.requests.includes('c1')).toBe(true);
  try {
    expect(await page.locator('.message').count()).toBe(1);
    expect(await page.locator('.message').getAttribute('data-message-id')).toBe('answer-c0');
    expect(await page.locator('#chat').evaluate(element => element.scrollTop)).toBe(1200);
  } finally { release(); }
  await expect(page.locator('.message')).toHaveAttribute('data-message-id', 'answer-c1');
  expect(await page.locator('#chat').evaluate(element => element.scrollTop)).toBe(0);
  await page.locator('#chat').evaluate(element => element.scrollTo(0, 800));
  await page.getByRole('button', { name: '对话 1', exact: true }).click();
  await expect(page.locator('.message')).toHaveAttribute('data-message-id', 'answer-c0');
  expect(await page.locator('#chat').evaluate(element => element.scrollTop)).toBe(1200);
  await page.getByRole('button', { name: '对话 2', exact: true }).click();
  await expect(page.locator('.message')).toHaveAttribute('data-message-id', 'answer-c1');
  expect(await page.locator('#chat').evaluate(element => element.scrollTop)).toBe(800);
});

test('clicking the active conversation keeps its DOM and does not reload or scroll', async ({ page }) => {
  const control = await setupNavigation(page);
  await page.evaluate(() => { document.querySelector('#chat').scrollTo(0, 900); window.originalMessage = document.querySelector('.message'); });
  await page.getByRole('button', { name: '对话 1', exact: true }).click();
  await page.waitForTimeout(250);
  expect(control.requests).toEqual(['c0']);
  expect(await page.evaluate(() => document.querySelector('.message') === window.originalMessage)).toBe(true);
  expect(await page.locator('#chat').evaluate(element => element.scrollTop)).toBe(900);
});

test('switching a long history keeps the list position and keyboard focus', async ({ page }) => {
  await setupNavigation(page);
  await page.locator('#conversationList').evaluate(element => { element.scrollTop = element.scrollHeight; });
  const top = await page.locator('#conversationList').evaluate(element => element.scrollTop);
  const target = page.getByRole('button', { name: '对话 18', exact: true });
  await target.click();
  await expect(page.locator('.message')).toHaveAttribute('data-message-id', 'answer-c17');
  expect(await page.locator('#conversationList').evaluate(element => element.scrollTop)).toBe(top);
  await expect(target).toBeFocused();
  expect(await page.locator('.message').evaluate(element => element.getAnimations({ subtree: true }).filter(animation => animation.playState === 'running').length)).toBe(0);
});

test('failed and out-of-order navigation preserve the last valid conversation', async ({ page }) => {
  const control = await setupNavigation(page);
  control.failures.add('c1');
  await page.locator('#chat').evaluate(element => element.scrollTo(0, 1100));
  await page.getByRole('button', { name: '对话 2', exact: true }).click();
  await expect(page.locator('#composerError')).toContainText('暂时无法加载');
  await expect(page.locator('.message')).toHaveAttribute('data-message-id', 'answer-c0');
  await expect(page.locator('.conversation-item.active')).toContainText('对话 1');
  expect(await page.locator('#chat').evaluate(element => element.scrollTop)).toBe(1100);
  control.failures.clear();
  const release = control.hold('c1');
  await page.getByRole('button', { name: '对话 2', exact: true }).click();
  await page.getByRole('button', { name: '对话 3', exact: true }).click();
  try { await expect(page.locator('.message')).toHaveAttribute('data-message-id', 'answer-c2'); }
  finally { release(); }
  await page.waitForTimeout(100);
  await expect(page.locator('.message')).toHaveAttribute('data-message-id', 'answer-c2');
  await expect(page.locator('.conversation-item.active')).toContainText('对话 3');
});

test('a reconnect snapshot and closing mobile history do not reset the reading position', async ({ page }) => {
  const control = await setupNavigation(page);
  await page.locator('#chat').evaluate(element => element.scrollTo(0, 1300));
  await page.evaluate(snapshot => window.__streams.at(-1).emit('conversation_snapshot', snapshot), control.snapshot('c0'));
  await page.waitForTimeout(100);
  expect(await page.locator('#chat').evaluate(element => element.scrollTop)).toBe(1300);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#chat').evaluate(element => element.scrollTo(0, 900));
  await page.locator('#openSidebar').click();
  await expect(page.locator('#sidebar')).toHaveClass(/open/);
  await page.keyboard.press('Escape');
  await expect(page.locator('#openSidebar')).toBeFocused();
  expect(await page.locator('#chat').evaluate(element => element.scrollTop)).toBe(900);
});

test('scrolling and switching long and short conversations keep the desktop frame fixed', async ({ page }) => {
  await setupNavigation(page, { shortIds: ['c1'] });
  const frame = () => page.evaluate(() => ['masthead', 'sidebar', 'composer'].map(id => {
    const rect = document.getElementById(id).getBoundingClientRect();
    return { id, x: rect.x, y: rect.y, width: rect.width, height: rect.height };
  }));
  const before = await frame();
  const chat = await page.locator('#chat').boundingBox();
  await page.mouse.move(chat.x + 50, chat.y + 80);
  await page.mouse.wheel(0, 800);
  await page.waitForTimeout(150);
  expect(await frame()).toEqual(before);
  const readingTop = await page.locator('#chat').evaluate(element => element.scrollTop);
  expect(readingTop).toBeGreaterThan(0);
  await page.getByRole('button', { name: '对话 2', exact: true }).click();
  await expect(page.locator('.message')).toHaveAttribute('data-message-id', 'answer-c1');
  expect(await frame()).toEqual(before);
  await page.getByRole('button', { name: '对话 1', exact: true }).click();
  await expect(page.locator('.message')).toHaveAttribute('data-message-id', 'answer-c0');
  expect(await frame()).toEqual(before);
  expect(await page.locator('#chat').evaluate(element => element.scrollTop)).toBe(readingTop);
  expect(await page.evaluate(() => scrollY)).toBe(0);
});

test('narrow history drawer and short answers never resize the underlying page', async ({ page }) => {
  await page.setViewportSize({ width: 319, height: 671 });
  await setupNavigation(page, { shortIds: ['c1'] });
  // 内置浏览器使用占据布局宽度的滚动条，不能只测浮层滚动条。
  await page.addStyleTag({ content: '::-webkit-scrollbar { width: 15px; height: 15px; }' });
  const frame = () => page.evaluate(() => ['masthead', 'mainContent', 'composer'].map(id => {
    const rect = document.getElementById(id).getBoundingClientRect();
    return { id, x: rect.x, y: rect.y, width: rect.width };
  }));
  const before = await frame();
  await page.locator('#openSidebar').click();
  expect(await frame()).toEqual(before);
  await page.getByRole('button', { name: '对话 2', exact: true }).click();
  await expect(page.locator('.message')).toHaveAttribute('data-message-id', 'answer-c1');
  await expect(page.locator('#sidebar')).toBeHidden();
  expect(await frame()).toEqual(before);
  expect(await page.evaluate(() => scrollY)).toBe(0);
});
