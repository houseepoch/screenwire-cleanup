import { expect, test } from '@playwright/test';
import { fixtureProjectId, generatingAssetsSnapshot, onboardingProject } from './fixtures/workflow-fixtures';
import { buildDesktopProject, mockDesktopBridge, readDesktopCallLog } from './helpers/desktop';
import { buildPersistedState, mockWorkflowApi, seedPersistedState } from './helpers/morpheus';

test('create-project flow validates required fields and enters onboarding on success', async ({ page }) => {
  const createdProject = buildDesktopProject({
    id: 'created-project',
    name: 'Neon Meridian',
    description: 'A neon chase through a collapsing skyline.',
    status: 'onboarding',
  });

  await mockDesktopBridge(page, {
    projects: [],
    createProjectResult: createdProject,
    selectProjectResult: {
      projectId: createdProject.id,
      projectDir: `/tmp/${createdProject.id}`,
      apiBaseUrl: 'http://127.0.0.1:8000',
    },
  });

  await page.goto('/');
  await page.getByTestId('home-start-creating').click();

  const submit = page.getByTestId('create-project-submit');
  await expect(submit).toBeDisabled();

  await page.getByTestId('create-project-name').fill(createdProject.name);
  await page.getByTestId('create-project-description').fill(createdProject.description);
  await expect(submit).toBeEnabled();

  await submit.click();

  await expect(page.getByTestId('onboarding-wizard')).toBeVisible();
  await expect(page.getByRole('heading', { name: createdProject.name })).toBeVisible();

  const calls = await readDesktopCallLog(page);
  expect(calls.createProject).toHaveLength(1);
  expect(calls.createProject[0]?.name).toBe(createdProject.name);
  expect(calls.selectProject).toEqual([createdProject.id]);
});

test('create-project flow surfaces desktop creation errors without leaving the modal', async ({ page }) => {
  await mockDesktopBridge(page, {
    projects: [],
    createProjectError: 'Project directory is not writable.',
  });

  await page.goto('/');
  await page.getByTestId('home-start-creating').click();
  await page.getByTestId('create-project-name').fill('Write-Protected Project');
  await page.getByTestId('create-project-submit').click();

  await expect(page.getByTestId('create-project-error')).toHaveText('Project directory is not writable.');
  await expect(page.getByTestId('create-project-modal')).toBeVisible();
});

test('see-how-it-works modal opens, advances, and closes from the home screen', async ({ page }) => {
  await page.goto('/');

  await page.getByTestId('home-see-how-it-works').click();
  await expect(page.getByTestId('how-it-works-modal')).toBeVisible();
  await expect(page.getByText('Upload Your Story')).toBeVisible();

  await page.getByTestId('how-it-works-next').click();
  await expect(page.getByText('AI Agent Analyzes')).toBeVisible();

  await page.getByTestId('how-it-works-close').click();
  await expect(page.getByTestId('how-it-works-modal')).toHaveCount(0);
});

test('existing project selection hydrates the workspace, shows worker state, and returns home', async ({ page }) => {
  const desktopProject = buildDesktopProject({
    ...generatingAssetsSnapshot.project,
    projectDir: `/tmp/${fixtureProjectId}`,
  });

  await mockDesktopBridge(page, {
    projects: [desktopProject],
  });
  await mockWorkflowApi(page, {
    initialSnapshot: generatingAssetsSnapshot,
    workers: [
      {
        id: 'storyboard_generation',
        name: 'Storyboard Generation',
        status: 'running',
        progress: 42,
        message: 'Rendering storyboard passes...',
      },
    ],
  });

  await page.goto('/');
  await page.getByTestId(`project-card-${fixtureProjectId}`).click();

  await expect(page.getByTestId('project-workspace')).toBeVisible();
  await expect(page.getByTestId('worker-overlay')).toBeVisible();
  await expect(page.getByText('Workers')).toBeVisible();

  await page.getByTestId('nav-back-to-projects').click();
  await expect(page.getByText('Recent productions')).toBeVisible();

  const calls = await readDesktopCallLog(page);
  expect(calls.returnToProjects).toBe(1);
});

test('project selection surfaces backend start failures on the home screen', async ({ page }) => {
  const desktopProject = buildDesktopProject({
    ...generatingAssetsSnapshot.project,
    projectDir: `/tmp/${fixtureProjectId}`,
  });

  await mockDesktopBridge(page, {
    projects: [desktopProject],
    selectProjectError: 'Project backend failed to start.',
  });

  await page.goto('/');
  await page.getByTestId(`project-card-${fixtureProjectId}`).click();

  await expect(page.getByTestId('home-action-error')).toHaveText('Project backend failed to start.');
  await expect(page.getByTestId('project-workspace')).toHaveCount(0);
});

test('onboarding submit surfaces backend failures and keeps the user in setup', async ({ page }) => {
  await mockDesktopBridge(page, {
    projects: [
      buildDesktopProject({
        ...onboardingProject,
        projectDir: `/tmp/${onboardingProject.id}`,
      }),
    ],
  });
  await seedPersistedState(
    page,
    buildPersistedState({
      projects: [onboardingProject],
      currentProject: onboardingProject,
      creativityLevel: onboardingProject.creativityLevel,
      generationMode: onboardingProject.generationMode,
    }),
  );

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.pathname === `/api/projects/${onboardingProject.id}/concept` && request.method() === 'POST') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
    }

    if (url.pathname === `/api/projects/${onboardingProject.id}/skeleton/generate` && request.method() === 'POST') {
      return route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ message: 'Skeleton generation failed while bootstrapping the project.' }),
      });
    }

    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    });
  });

  await page.goto('/');
  await page.getByTestId('onboarding-idea').fill('A contained sci-fi chase through a blackout district.');
  await page.getByTestId('onboarding-next').click();
  await page.getByTestId('onboarding-submit').click();

  await expect(page.getByTestId('onboarding-submit-error')).toHaveText(
    'Skeleton generation failed while bootstrapping the project.',
  );
  await expect(page.getByTestId('onboarding-wizard')).toBeVisible();
});
