import { expect, test } from '@playwright/test';
import { onboardingProject } from './fixtures/workflow-fixtures';
import { buildPersistedState, seedPersistedState } from './helpers/morpheus';

test('onboarding step 1 gates progression until story input exists', async ({ page }) => {
  await seedPersistedState(
    page,
    buildPersistedState({
      projects: [onboardingProject],
      currentProject: onboardingProject,
      creativityLevel: onboardingProject.creativityLevel,
      generationMode: onboardingProject.generationMode,
    }),
  );

  await page.goto('/');

  const nextButton = page.getByTestId('onboarding-next');
  await expect(page.getByTestId('onboarding-wizard')).toBeVisible();
  await expect(nextButton).toBeDisabled();

  await page.getByTestId('onboarding-idea').fill('Build a storm-lit clocktower chase with a sharp sci-fi noir tone.');
  await expect(nextButton).toBeEnabled();

  await nextButton.click();

  await expect(page.getByText('How much creative freedom?')).toBeVisible();
  await expect(page.getByTestId('onboarding-submit')).toBeVisible();
});
