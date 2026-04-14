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
  await expect(page.getByTestId('workflow-continue-button')).toBeDisabled();
});

test('reference gate advances to frame generation from the top-bar continue button', async ({ page }) => {
  await mockWorkflowApi(page, {
    initialSnapshot: referenceReviewSnapshot,
    approvalResponses: {
      references: generatingFramesSnapshot,
    },
  });
  await seedPersistedState(page, buildPersistedStateFromSnapshot(referenceReviewSnapshot));

  await page.goto('/');

  await expect(page.getByTestId('workflow-continue-button')).toBeEnabled();
  await page.getByTestId('workflow-continue-button').click();

  await expect(page.getByTestId('workflow-continue-button')).toBeDisabled();
  await expect(page.getByTestId('worker-overlay')).toBeVisible();
});

test('reference review exposes explicit entity image drop targets on every entity card', async ({ page }) => {
  await mockWorkflowApi(page, {
    initialSnapshot: referenceReviewSnapshot,
  });
  await seedPersistedState(page, buildPersistedStateFromSnapshot(referenceReviewSnapshot));

  await page.goto('/');
  await page.getByRole('button', { name: 'Cast' }).click();
  await expect(page.getByTestId('entity-dropzone-cast-mara')).toBeVisible();

  await page.getByRole('button', { name: 'Locations' }).click();
  await expect(page.getByTestId('entity-dropzone-location-clocktower')).toBeVisible();

  await page.getByRole('button', { name: 'Props' }).click();
  await expect(page.getByTestId('entity-dropzone-prop-key')).toBeVisible();
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

  await expect(page.getByTestId('workflow-continue-button')).toBeEnabled();
  await expect(page.getByTestId('timeline-bar')).toBeVisible();
  await expect(page.getByTestId('timeline-linear-strip')).toBeVisible();
  await expect(page.getByTestId('timeline-dialogue-overlay-frame-1')).toBeVisible();

  await page.getByTestId('timeline-media-toggle-prompt').click();
  await expect(page.getByText('Mara runs through rain-slick neon alley toward the clocktower.')).toBeVisible();

  await page.getByTestId('timeline-media-toggle-image').click();
  await expect(page.locator('img[alt="Frame 1"]')).toBeVisible();
  await page.getByTestId('timeline-dialogue-toggle').click();
  await expect(page.getByTestId('timeline-dialogue-overlay-frame-1')).toHaveCount(0);
  await page.getByTestId('timeline-dialogue-toggle').click();
  await expect(page.getByTestId('timeline-dialogue-overlay-frame-1')).toBeVisible();

  await page.getByTestId('workflow-continue-button').click();

  await expect(page.getByTestId('workflow-continue-button')).toBeDisabled();
  await expect(page.getByTestId('worker-overlay')).toBeVisible();
});
