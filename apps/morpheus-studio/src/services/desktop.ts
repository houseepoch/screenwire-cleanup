import type { Project } from '../types';
import { configureBackendBase } from './api';
import type {
  DesktopBackendState,
  DesktopCreateProjectRequest,
  DesktopProjectSummary,
} from '../types/electron';

function toProject(summary: DesktopProjectSummary): Project {
  return {
    id: summary.id,
    name: summary.name,
    description: summary.description,
    status: (summary.status as Project['status']) || 'draft',
    createdAt: new Date(summary.createdAt),
    updatedAt: new Date(summary.updatedAt),
    creativityLevel: summary.creativityLevel,
    generationMode: summary.generationMode,
    progress: summary.progress,
    coverImageUrl: summary.coverImageUrl ?? null,
    coverSummary: summary.coverSummary ?? null,
  };
}

export const desktopService = {
  isElectronHost(): boolean {
    return typeof navigator !== 'undefined' && /Electron/i.test(navigator.userAgent || '');
  },

  isAvailable(): boolean {
    return Boolean(window.screenwire);
  },

  async listProjects(): Promise<Project[]> {
    if (!window.screenwire) {
      return [];
    }
    const projects = await window.screenwire.listProjects();
    return projects.map(toProject);
  },

  async createProject(payload: DesktopCreateProjectRequest): Promise<Project | null> {
    if (!window.screenwire) {
      return null;
    }
    const project = await window.screenwire.createProject(payload);
    return project ? toProject(project) : null;
  },

  async selectProject(projectId: string): Promise<{ projectId: string; apiBaseUrl: string } | null> {
    if (!window.screenwire) {
      return null;
    }
    const result = await window.screenwire.selectProject(projectId);
    if (!result) {
      return null;
    }
    configureBackendBase(result.apiBaseUrl);
    return { projectId: result.projectId, apiBaseUrl: result.apiBaseUrl };
  },

  async getBackendState(): Promise<DesktopBackendState | null> {
    if (!window.screenwire) {
      return null;
    }
    const state = await window.screenwire.getBackendState();
    if (state?.running && state.apiBaseUrl) {
      configureBackendBase(state.apiBaseUrl);
    }
    return state;
  },

  async returnToProjects(): Promise<DesktopBackendState | null> {
    if (!window.screenwire) {
      return null;
    }
    const state = await window.screenwire.returnToProjects();
    return state;
  },

  async openProjectFolder(projectId: string): Promise<void> {
    if (!window.screenwire) {
      return;
    }
    await window.screenwire.openProjectFolder(projectId);
  },
};
