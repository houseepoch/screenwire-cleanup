import { expect, test, type Locator } from '@playwright/test';
import { fixtureProjectId, generatingAssetsSnapshot } from './fixtures/workflow-fixtures';
import { buildDesktopProject, mockDesktopBridge } from './helpers/desktop';
import { mockWorkflowApi } from './helpers/morpheus';

async function expectBoundingBox(locator: Locator) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  return box!;
}

test('home screen swaps desktop and mobile project rails across the mobile breakpoint', async ({ page }) => {
  const desktopProject = buildDesktopProject({
    ...generatingAssetsSnapshot.project,
    projectDir: `/tmp/${fixtureProjectId}`,
  });

  await mockDesktopBridge(page, {
    projects: [desktopProject],
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');

  await expect(page.getByTestId('new-project-card')).toBeVisible();
  await expect(page.getByTestId(`project-card-${fixtureProjectId}`)).toBeVisible();
  await expect(page.getByTestId('new-project-card-mobile')).not.toBeVisible();

  await page.setViewportSize({ width: 540, height: 900 });

  await expect(page.getByTestId('new-project-card-mobile')).toBeVisible();
  await expect(page.getByTestId(`project-card-mobile-${fixtureProjectId}`)).toBeVisible();
  await expect(page.getByTestId('new-project-card')).not.toBeVisible();
});

test('create-project modal remains fully visible inside a short mobile viewport', async ({ page }) => {
  await mockDesktopBridge(page, { projects: [] });

  await page.setViewportSize({ width: 540, height: 620 });
  await page.goto('/');
  await page.getByTestId('home-start-creating').click();

  const dialog = page.getByTestId('create-project-dialog');
  await expect(dialog).toBeVisible();

  const box = await expectBoundingBox(dialog);
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(540);
  expect(box.y + box.height).toBeLessThanOrEqual(620);
});

test('workspace reflows from desktop split view to tablet stack and mobile shell on resize', async ({ page }) => {
  const desktopProject = buildDesktopProject({
    ...generatingAssetsSnapshot.project,
    projectDir: `/tmp/${fixtureProjectId}`,
  });

  await mockDesktopBridge(page, {
    projects: [desktopProject],
  });
  await mockWorkflowApi(page, {
    initialSnapshot: generatingAssetsSnapshot,
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await page.getByTestId(`project-card-${fixtureProjectId}`).click();

  await expect(page.getByTestId('project-workspace')).toBeVisible();
  await expect(page.getByTestId('timeline-bar')).toBeVisible();
  await expect(page.getByTestId('detail-panel')).toBeVisible();
  await expect(page.getByTestId('agent-panel')).toBeVisible();

  const detailDesktop = await expectBoundingBox(page.getByTestId('detail-panel'));
  const agentDesktop = await expectBoundingBox(page.getByTestId('agent-panel'));
  expect(agentDesktop.x).toBeGreaterThan(detailDesktop.x + 100);
  expect(Math.abs(agentDesktop.y - detailDesktop.y)).toBeLessThan(40);

  await page.setViewportSize({ width: 1100, height: 900 });

  await expect(page.getByTestId('project-workspace')).toBeVisible();
  const detailTablet = await expectBoundingBox(page.getByTestId('detail-panel'));
  const agentTablet = await expectBoundingBox(page.getByTestId('agent-panel'));
  expect(Math.abs(agentTablet.x - detailTablet.x)).toBeLessThan(20);
  expect(agentTablet.y).toBeGreaterThan(detailTablet.y + 120);

  await page.setViewportSize({ width: 700, height: 900 });

  await expect(page.getByTestId('project-workspace-mobile')).toBeVisible();
  await expect(page.getByTestId('project-workspace')).toHaveCount(0);
  await expect(page.getByTestId('mobile-agent-chat')).toBeVisible();
  await page.getByTestId('mobile-chat-open-timeline').click();
  await expect(page.getByTestId('mobile-timeline-tray')).toBeVisible();
  await page.getByTestId('mobile-timeline-close').click();
  await expect(page.getByTestId('mobile-timeline-tray')).toHaveCount(0);
  await page.getByTestId('mobile-chat-open-details').click();
  await expect(page.getByTestId('mobile-detail-view')).toBeVisible();
  await page.getByTestId('mobile-detail-open-chat').click();
  await expect(page.getByTestId('mobile-agent-chat')).toBeVisible();

  await page.setViewportSize({ width: 1280, height: 900 });

  await expect(page.getByTestId('project-workspace')).toBeVisible();
  await expect(page.getByTestId('timeline-bar')).toBeVisible();
  await expect(page.getByTestId('project-workspace-mobile')).toHaveCount(0);
});
