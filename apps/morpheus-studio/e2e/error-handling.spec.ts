import { expect, test } from '@playwright/test';
import { referenceReviewSnapshot, timelineReviewSnapshot } from './fixtures/workflow-fixtures';
import { buildPersistedStateFromSnapshot, mockWorkflowApi, seedPersistedState } from './helpers/morpheus';

test('shows an inline error when a workflow approval request fails', async ({ page }) => {
  await mockWorkflowApi(page, {
    initialSnapshot: referenceReviewSnapshot,
    approvalResponses: {
      references: {
        status: 500,
        message: 'Approval pipeline is temporarily unavailable.',
      },
    },
  });
  await seedPersistedState(page, buildPersistedStateFromSnapshot(referenceReviewSnapshot));

  await page.goto('/');
  await page.getByTestId('workflow-continue-button').click();

  await expect(page.getByTestId('workflow-continue-error')).toHaveText('Approval pipeline is temporarily unavailable.');
});

test('shows an inline error when the top-bar continue action fails during timeline review', async ({ page }) => {
  await mockWorkflowApi(page, {
    initialSnapshot: timelineReviewSnapshot,
    approvalResponses: {
      timeline: {
        status: 500,
        message: 'Timeline approval failed while the render queue is locked.',
      },
    },
  });
  await seedPersistedState(page, buildPersistedStateFromSnapshot(timelineReviewSnapshot));

  await page.goto('/');
  await page.getByTestId('workflow-continue-button').click();

  await expect(page.getByTestId('workflow-continue-error')).toHaveText('Timeline approval failed while the render queue is locked.');
});
