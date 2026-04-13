import type { Page, Route } from '@playwright/test';

type Gate = 'skeleton' | 'references' | 'timeline' | 'video';

type WorkflowSnapshot = {
  project: {
    id: string;
    creativityLevel: string;
    generationMode: string;
  };
  creativeConcept: unknown;
  skeletonPlan: unknown;
  scriptText: string;
  entities: unknown[];
  storyboardFrames: unknown[];
  timelineFrames: unknown[];
  dialogueBlocks: unknown[];
  workflow: {
    approvals: Record<string, string>;
    changeRequests: Array<{
      gate: string;
      feedback: string;
      timestamp: string;
    }>;
  };
  reports?: Record<string, unknown>;
};

type PersistedState = {
  projects: unknown[];
  currentProject: unknown | null;
  creativeConcept: unknown;
  creativityLevel: string;
  generationMode: string;
  skeletonPlan: unknown;
  scriptText: string;
  entities: unknown[];
  storyboardFrames: unknown[];
  timelineFrames: unknown[];
  dialogueBlocks: unknown[];
  videoExports: unknown[];
  workflow: {
    approvals: Record<string, string>;
    changeRequests: Array<{
      gate: string;
      feedback: string;
      timestamp: string;
    }>;
  };
  reports: Record<string, unknown>;
};

type ErrorResponse = {
  status: number;
  message: string;
};

export type WorkflowApiScenario = {
  initialSnapshot: WorkflowSnapshot;
  workers?: unknown[];
  approvalResponses?: Partial<Record<Gate, WorkflowSnapshot | ErrorResponse>>;
  changeRequestResponses?: Partial<Record<Gate, { status?: number; message?: string }>>;
};

const STORAGE_KEY = 'morpheus-storage';
const FIXTURE_TIMESTAMP = '2026-04-12T20:00:00.000Z';

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function fulfillJson(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

export function buildPersistedState(overrides: Partial<PersistedState>): PersistedState {
  return {
    projects: [],
    currentProject: null,
    creativeConcept: null,
    creativityLevel: 'balanced',
    generationMode: 'assisted',
    skeletonPlan: {
      scenes: [],
      totalScenes: 0,
      estimatedDuration: 0,
    },
    scriptText: '',
    entities: [],
    storyboardFrames: [],
    timelineFrames: [],
    dialogueBlocks: [],
    videoExports: [],
    workflow: {
      approvals: {},
      changeRequests: [],
    },
    reports: {},
    ...overrides,
  };
}

export function buildPersistedStateFromSnapshot(snapshot: WorkflowSnapshot): PersistedState {
  return buildPersistedState({
    projects: [snapshot.project],
    currentProject: snapshot.project,
    creativeConcept: snapshot.creativeConcept,
    creativityLevel: snapshot.project.creativityLevel,
    generationMode: snapshot.project.generationMode,
    skeletonPlan: snapshot.skeletonPlan,
    scriptText: snapshot.scriptText,
    entities: snapshot.entities,
    storyboardFrames: snapshot.storyboardFrames,
    timelineFrames: snapshot.timelineFrames,
    dialogueBlocks: snapshot.dialogueBlocks,
    workflow: snapshot.workflow,
    reports: snapshot.reports || {},
  });
}

export async function seedPersistedState(page: Page, state: PersistedState): Promise<void> {
  const persisted = {
    state,
    version: 2,
  };

  await page.addInitScript(
    ([storageKey, payload]) => {
      window.localStorage.setItem(storageKey, payload);
    },
    [STORAGE_KEY, JSON.stringify(persisted)],
  );
}

export async function mockWorkflowApi(page: Page, scenario: WorkflowApiScenario): Promise<void> {
  let currentSnapshot = clone(scenario.initialSnapshot);
  const workers = clone(scenario.workers || []);
  const projectId = currentSnapshot.project.id;

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (url.pathname === `/api/projects/${projectId}/workspace` && request.method() === 'GET') {
      return fulfillJson(route, currentSnapshot);
    }

    if (url.pathname === `/api/projects/${projectId}/workers` && request.method() === 'GET') {
      return fulfillJson(route, workers);
    }

    if (url.pathname === `/api/projects/${projectId}/approve` && request.method() === 'POST') {
      const body = JSON.parse(request.postData() || '{}') as { gate?: Gate };
      const gate = body.gate as Gate | undefined;
      const response = gate ? scenario.approvalResponses?.[gate] : undefined;

      if (response && 'status' in response && typeof response.status === 'number') {
        return fulfillJson(route, { message: response.message }, response.status);
      }

      if (response) {
        currentSnapshot = clone(response);
      }

      return fulfillJson(route, currentSnapshot);
    }

    if (url.pathname === `/api/projects/${projectId}/request-changes` && request.method() === 'POST') {
      const body = JSON.parse(request.postData() || '{}') as { gate?: Gate; feedback?: string };
      const gate = body.gate as Gate | undefined;
      const response = gate ? scenario.changeRequestResponses?.[gate] : undefined;

      if (response?.status && response.status >= 400) {
        return fulfillJson(route, { message: response.message || 'Request failed' }, response.status);
      }

      const changeRequests = [
        ...currentSnapshot.workflow.changeRequests,
        {
          gate: gate || 'unknown',
          feedback: body.feedback || '',
          timestamp: FIXTURE_TIMESTAMP,
        },
      ];

      currentSnapshot = {
        ...currentSnapshot,
        workflow: {
          ...currentSnapshot.workflow,
          changeRequests,
        },
      };

      return fulfillJson(route, {
        ok: true,
        changeRequests,
      });
    }

    return fulfillJson(route, {});
  });
}
