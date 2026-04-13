import { expect, test } from '@playwright/test';
import {
  generatingAssetsSnapshot,
  generatingFramesSnapshot,
  generatingVideoSnapshot,
  referenceReviewSnapshot,
  timelineReviewSnapshot,
} from './fixtures/workflow-fixtures';
import { buildPersistedStateFromSnapshot, mockWorkflowApi, seedPersistedState } from './helpers/morpheus';

test('preproduction build keeps the workspace in a loading state until references are ready', async ({ page }) => {
  await mockWorkflowApi(page, {
    initialSnapshot: generatingAssetsSnapshot,
  });
  await seedPersistedState(page, buildPersistedStateFromSnapshot(generatingAssetsSnapshot));

  await page.goto('/');

  await expect(page.getByTestId('worker-overlay')).toBeVisible();
  await expect(page.getByTestId('workflow-approval-bar')).toHaveCount(0);
});

test('reference gate supports change requests and advances to frame generation', async ({ page }) => {
  await mockWorkflowApi(page, {
    initialSnapshot: referenceReviewSnapshot,
    approvalResponses: {
      references: generatingFramesSnapshot,
    },
  });
  await seedPersistedState(page, buildPersistedStateFromSnapshot(referenceReviewSnapshot));

  await page.goto('/');

  await expect(page.getByTestId('workflow-approval-title')).toHaveText('References approval gate');

  page.once('dialog', async (dialog) => {
    await dialog.accept('Tighten the opening beat before approval.');
  });
  await page.getByTestId('workflow-request-changes-button').click();

  await expect(page.getByTestId('workflow-approval-subtitle')).toContainText('1 change request(s) queued');

  await page.getByTestId('workflow-approve-button').click();

  await expect(page.getByTestId('workflow-approval-bar')).toHaveCount(0);
  await expect(page.getByTestId('worker-overlay')).toBeVisible();
});

test('timeline gate keeps media toggles working and advances to video generation', async ({ page }) => {
  await mockWorkflowApi(page, {
    initialSnapshot: timelineReviewSnapshot,
    approvalResponses: {
      timeline: generatingVideoSnapshot,
    },
  });
  await seedPersistedState(page, buildPersistedStateFromSnapshot(timelineReviewSnapshot));

  await page.goto('/');

  await expect(page.getByTestId('workflow-approval-title')).toHaveText('Timeline approval gate');
  await expect(page.getByTestId('timeline-bar')).toBeVisible();

  await page.getByTestId('timeline-media-toggle-prompt').click();
  await expect(page.getByText('Mara runs through rain-slick neon alley toward the clocktower.')).toBeVisible();

  await page.getByTestId('timeline-media-toggle-image').click();
  await expect(page.locator('img[alt="Frame 1"]')).toBeVisible();

  await page.getByTestId('workflow-approve-button').click();

  await expect(page.getByTestId('workflow-approval-bar')).toHaveCount(0);
  await expect(page.getByTestId('worker-overlay')).toBeVisible();
});
