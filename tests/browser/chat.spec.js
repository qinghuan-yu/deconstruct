const { test, expect } = require('@playwright/test');

const assistant = (content, mode = 'quick', status = 'complete') => ({
  id: 'answer', role: 'assistant', content, mode, status, created_at: 1794052800, steps: {}, attachments: [],
});

async function setup(page, messages = [], cursor = 42) {
  await page.addInitScript(() => {
    window.__streams = [];
    window.EventSource = class {
      constructor(url) { this.url = url; this.listeners = {}; window.__streams.push(this); }
      addEventListener(name, fn) { this.listeners[name] = fn; }
      close() { this.closed = true; }
      emit(type, payload, seq = 43) { this.listeners[type]?.({ data: JSON.stringify({ type, payload, seq, message_id: 'answer' }) }); }
    };
  });
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body;
    if (path === '/api/settings/model') body = { configured: false, base_url: 'https://api.deepseek.com', model: 'deepseek-chat' };
    else if (path === '/api/conversations') body = { conversations: [{ id: 'sample', title: '本周的试运行', updated_at: 1794052800 }] };
    else body = { conversation: { id: 'sample', title: '本周的试运行' }, messages, cursor };
    await route.fulfill({ json: body });
  });
  await page.goto('/');
  await expect.poll(() => page.evaluate(() => window.__streams.length)).toBeGreaterThan(0);
}

test('quick answers render ordered lists, code and safe links as the answer', async ({ page }) => {
  await setup(page, [assistant('1. 明确预算\n2. 验证方案\n\n```js\nconst value = "<safe>";\n```\n\n> 保留余量\n\n<img src=x onerror="window.hacked=true">')]);
  await expect(page.locator('.final-answer ol li')).toHaveCount(2);
  await expect(page.locator('.final-answer pre code')).toContainText('<safe>');
  await expect(page.locator('.final-answer blockquote')).toContainText('保留余量');
  expect(await page.evaluate(() => window.hacked)).toBeUndefined();
  await expect(page.locator('.final-answer img')).toHaveCount(0);
});

test('snapshot cursor avoids replaying the complete conversation', async ({ page }) => {
  await setup(page, [assistant('已经完成')]);
  expect(await page.evaluate(() => window.__streams.at(-1).url)).toContain('after=42');
});

test('audit progress updates and expansion survives streamed output', async ({ page }) => {
  await setup(page, [assistant('', 'expert', 'streaming')]);
  await page.evaluate(() => window.__streams.at(-1).emit('debate_step_started', { key: 'red_team', title: '红队质询员', status: 'running' }));
  await expect(page.locator('.audit-label')).toContainText('红队质询员');
  await expect(page.locator('.audit-progress > [data-status="running"]')).toHaveCount(1);
  await page.locator('.audit-panel > summary').click();
  await expect(page.locator('.audit-panel')).toHaveAttribute('open', '');
  await page.evaluate(() => window.__streams.at(-1).emit('assistant_delta', { delta: '# 初步结论\n\n可以先做小规模验证。' }, 44));
  await expect(page.locator('.final-answer')).toContainText('可以先做');
  await expect(page.locator('.audit-panel')).toHaveAttribute('open', '');
  await page.locator('.audit-panel > summary').click();
  await page.evaluate(() => window.__streams.at(-1).emit('assistant_delta', { delta: '保留预算。' }, 45));
  await expect(page.locator('.audit-panel')).not.toHaveAttribute('open', '');
  await page.evaluate(() => window.__streams.at(-1).emit('debate_step_completed', { key: 'red_team', title: '红队质询员', content: '检查预算', elapsed_seconds: 3 }, 46));
  await expect(page.locator('.audit-progress > [data-status="complete"]')).toHaveCount(1);
  await expect(page.locator('.audit-progress > [data-status="running"]')).toHaveCount(0);
});

test('failed sends retain draft and show a recoverable error', async ({ page }) => {
  await setup(page);
  await page.route('**/api/conversations/sample/messages', route => route.fulfill({ status: 503, json: { detail: '连接暂不可用' } }));
  await page.locator('#promptInput').fill('不要丢失这段草稿');
  await page.locator('#sendButton').click();
  await expect(page.locator('#promptInput')).toHaveValue('不要丢失这段草稿');
  await expect(page.locator('#composerError')).toContainText('连接暂不可用');
});

test('mobile history closes with Escape and backdrop without overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  await page.locator('#openSidebar').click();
  await expect(page.locator('#sidebar')).toHaveClass(/open/);
  expect((await page.locator('#sidebar').boundingBox()).height).toBe(844);
  await page.keyboard.press('Escape');
  await expect(page.locator('#sidebar')).not.toHaveClass(/open/);
  await page.locator('#openSidebar').click();
  await page.locator('#sidebarBackdrop').click({ position: { x: 5, y: 5 } });
  await expect(page.locator('#sidebar')).not.toHaveClass(/open/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('streaming does not pull a reader away from an earlier paragraph', async ({ page }) => {
  await setup(page, [assistant('段落。\n\n'.repeat(100), 'quick', 'streaming')]);
  await page.locator('#chat').evaluate(element => element.scrollTo(0, 200));
  await page.waitForTimeout(300);
  const before = await page.locator('#chat').evaluate(element => element.scrollTop);
  expect(before).toBeGreaterThan(0);
  await page.evaluate(() => window.__streams.at(-1).emit('assistant_delta', { delta: '新增内容。' }));
  await page.waitForTimeout(400);
  expect(Math.abs(await page.locator('#chat').evaluate(element => element.scrollTop) - before)).toBeLessThan(5);
});

test('duplicate stream events do not duplicate answer text', async ({ page }) => {
  await setup(page, [assistant('', 'quick', 'streaming')]);
  await page.evaluate(() => {
    const stream = window.__streams.at(-1);
    stream.emit('assistant_delta', { delta: '只出现一次。' }, 43);
    stream.emit('assistant_delta', { delta: '只出现一次。' }, 43);
  });
  await expect(page.locator('.final-answer')).toHaveText('只出现一次。');
});

test('a reader can keep selected text while more output arrives', async ({ page }) => {
  await setup(page, [assistant('可以复制的文字。', 'quick', 'streaming')]);
  await page.locator('.final-answer p').waitFor();
  await page.evaluate(() => {
    const range = document.createRange();
    range.selectNodeContents(document.querySelector('.final-answer p'));
    const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
    window.__streams.at(-1).emit('assistant_delta', { delta: '\n\n追加的段落。' });
  });
  await page.waitForTimeout(200);
  expect(await page.evaluate(() => getSelection().toString())).toBe('可以复制的文字。');
  await page.evaluate(() => getSelection().removeAllRanges());
  await expect(page.locator('.final-answer')).toContainText('追加的段落');
});

test('reading and composer fit narrow mobile, tablet and desktop widths', async ({ page }) => {
  await setup(page, [assistant('# 决策书\n\n| 事项 | 计划 | 原因 | 标准 |\n|---|---|---|---|\n| 本周 | 可执行方案 | 依据 | 验收 |')]);
  for (const width of [320, 390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.waitForTimeout(100);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const button = await page.locator('#sendButton').boundingBox();
    expect(button.x + button.width).toBeLessThanOrEqual(width);
  }
});

test('composer starts near the heading and docks without losing the draft', async ({ page }) => {
  await setup(page);
  await page.locator('#promptInput').fill('仍在编辑的草稿');
  const initial = await page.locator('#composer').boundingBox();
  expect(initial.y).toBeLessThan(250);
  await page.evaluate(() => window.__streams.at(-1).emit('assistant_message_started', {
    message: { id: 'answer', role: 'assistant', content: '', mode: 'quick', status: 'streaming', steps: {} },
  }));
  await expect(page.locator('body')).not.toHaveClass(/is-empty/);
  await expect(page.locator('#promptInput')).toHaveValue('仍在编辑的草稿');
  const docked = await page.locator('#composer').boundingBox();
  expect(docked.y).toBeGreaterThan(initial.y);
  expect(docked.y + docked.height).toBeLessThanOrEqual(960);
  await page.locator('#newChat').click();
  await expect(page.locator('body')).toHaveClass(/is-empty/);
  expect((await page.locator('#composer').boundingBox()).y).toBeLessThan(250);
});

test('streaming reveals the answer once and does not replay motion per delta', async ({ page }) => {
  await setup(page, [assistant('', 'quick', 'streaming')]);
  await page.evaluate(() => {
    window.answerReveals = 0;
    document.querySelector('.final-answer').addEventListener('animationstart', () => window.answerReveals++);
    window.__streams.at(-1).emit('assistant_delta', { delta: '第一段。' });
  });
  await expect.poll(() => page.evaluate(() => window.answerReveals)).toBe(1);
  await page.evaluate(() => window.__streams.at(-1).emit('assistant_delta', { delta: '\n\n第二段。' }, 44));
  await expect(page.locator('.final-answer')).toContainText('第二段');
  await page.waitForTimeout(350);
  expect(await page.evaluate(() => window.answerReveals)).toBe(1);
});

test('rapid drawer reversal restores focus and cannot leave the page inert', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  await page.locator('#openSidebar').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#closeSidebar')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.locator('#openSidebar')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('#sidebar')).toHaveClass(/open/);
  await page.keyboard.press('Escape');
  await expect(page.locator('#sidebar')).toBeHidden();
  await page.locator('#promptInput').fill('关闭后可以继续输入');
  await page.setViewportSize({ width: 1024, height: 900 });
  await expect(page.locator('#sidebar')).toBeVisible();
  await page.locator('#modelSettings > summary').click();
  await page.locator('#modelSettings > summary').click();
  await page.locator('#modelInput').fill('deepseek-chat');
});

test('reduced motion keeps drawers and audit records usable without animation', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 390, height: 844 });
  const message = assistant('已经完成。', 'expert');
  message.steps = { red_team: { title: '红队质询员', status: 'complete', content: '需要核实预算。' } };
  await setup(page, [message]);
  await page.locator('.audit-panel > summary').click();
  await page.locator('.audit-step > summary').click();
  await expect(page.locator('.audit-step-content')).toBeVisible();
  await page.locator('#openSidebar').click();
  await expect(page.locator('#closeSidebar')).toBeFocused();
  expect(await page.evaluate(() => document.getAnimations().filter(animation => animation.playState === 'running').length)).toBe(0);
  await page.keyboard.press('Escape');
  await expect(page.locator('#sidebar')).toBeHidden();
  await page.locator('#promptInput').fill('可以继续提问');
});
