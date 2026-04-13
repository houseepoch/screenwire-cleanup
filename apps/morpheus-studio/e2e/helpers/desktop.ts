import type { Page } from '@playwright/test';
import type {
  DesktopBackendState,
  DesktopCreateProjectRequest,
  DesktopProjectSummary,
} from '../../src/types/electron';

type DesktopSelectProjectResult = {
  projectId: string;
  projectDir: string;
  apiBaseUrl: string;
};

type DesktopBridgeScenario = {
  projects?: DesktopProjectSummary[];
  createProjectResult?: DesktopProjectSummary | null;
  createProjectError?: string;
  selectProjectResult?: DesktopSelectProjectResult | null;
  selectProjectError?: string;
  backendState?: DesktopBackendState;
};

export type DesktopBridgeCallLog = {
  createProject: DesktopCreateProjectRequest[];
  selectProject: string[];
  listProjects: number;
};

declare global {
  interface Window {
    __screenwireCalls?: DesktopBridgeCallLog;
  }
}

const FIXTURE_TIMESTAMP = '2026-04-12T20:00:00.000Z';
export function buildDesktopProject(overrides: Partial<DesktopProjectSummary> = {}): DesktopProjectSummary {
  return {
    id: 'desktop-project',
    name: 'Desktop Fixture Project',
    description: 'Desktop bridge fixture project.',
    status: 'onboarding',
    createdAt: FIXTURE_TIMESTAMP,
    updatedAt: FIXTURE_TIMESTAMP,
    creativityLevel: 'balanced',
    generationMode: 'assisted',
    progress: 0,
    projectDir: '/tmp/desktop-project',
    coverImageUrl: null,
    coverSummary: null,
    ...overrides,
  };
}

export async function mockDesktopBridge(page: Page, scenario: DesktopBridgeScenario): Promise<void> {
  await page.addInitScript((bridgeScenario) => {
    const calls = {
      createProject: [],
      selectProject: [],
      listProjects: 0,
    };

    window.__screenwireCalls = calls;

    const apiBaseUrl = bridgeScenario.backendState?.apiBaseUrl || 'http://127.0.0.1:8000';

    window.screenwire = {
      async listProjects() {
        calls.listProjects += 1;
        return bridgeScenario.projects || [];
      },
      async createProject(payload) {
        calls.createProject.push(payload);
        if (bridgeScenario.createProjectError) {
          throw new Error(bridgeScenario.createProjectError);
        }
        if (bridgeScenario.createProjectResult === null) {
          return null;
        }
        if (bridgeScenario.createProjectResult) {
          return bridgeScenario.createProjectResult;
        }
        return {
          id: `created-${Date.now()}`,
          name: payload.name,
          description: payload.description || '',
          status: 'onboarding',
          createdAt: '2026-04-12T20:00:00.000Z',
          updatedAt: '2026-04-12T20:00:00.000Z',
          creativityLevel: payload.creativityLevel || 'balanced',
          generationMode: 'assisted',
          progress: 0,
          projectDir: `/tmp/${String(payload.name || 'project')
            .toLowerCase()
            .replace(/\s+/g, '_')}`,
          coverImageUrl: null,
          coverSummary: null,
        };
      },
      async selectProject(projectId) {
        calls.selectProject.push(projectId);
        if (bridgeScenario.selectProjectError) {
          throw new Error(bridgeScenario.selectProjectError);
        }
        if (bridgeScenario.selectProjectResult === null) {
          return null;
        }
        if (bridgeScenario.selectProjectResult) {
          return bridgeScenario.selectProjectResult;
        }
        return {
          projectId,
          projectDir: `/tmp/${projectId}`,
          apiBaseUrl,
        };
      },
      async getBackendState() {
        return (
          bridgeScenario.backendState || {
            currentProjectId: null,
            apiBaseUrl,
            running: false,
          }
        );
      },
      async openProjectFolder() {
        return '';
      },
      async chooseFile() {
        return null;
      },
    };
  }, scenario);
}

export async function readDesktopCallLog(page: Page): Promise<DesktopBridgeCallLog> {
  return page.evaluate(() => window.__screenwireCalls as DesktopBridgeCallLog);
}
