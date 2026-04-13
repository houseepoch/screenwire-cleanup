export interface DesktopProjectSummary {
  id: string;
  name: string;
  description: string;
  status: string;
  createdAt: string;
  updatedAt: string;
  creativityLevel: 'strict' | 'balanced' | 'creative' | 'unbounded';
  generationMode: 'guided' | 'assisted' | 'open';
  progress: number;
  projectDir: string;
  coverImageUrl?: string | null;
  coverSummary?: string | null;
}

export interface DesktopCreateProjectRequest {
  name: string;
  description?: string;
  creativityLevel?: 'strict' | 'balanced' | 'creative' | 'unbounded';
  frameBudget?: string | number;
  mediaStyle?: string;
  seedFile?: string;
}

export interface DesktopBackendState {
  currentProjectId: string | null;
  apiBaseUrl: string;
  running: boolean;
}

export interface ScreenwireBridge {
  listProjects(): Promise<DesktopProjectSummary[]>;
  createProject(payload: DesktopCreateProjectRequest): Promise<DesktopProjectSummary>;
  selectProject(projectId: string): Promise<{
    projectId: string;
    projectDir: string;
    apiBaseUrl: string;
  }>;
  getBackendState(): Promise<DesktopBackendState>;
  openProjectFolder(projectId: string): Promise<string>;
  chooseFile(): Promise<string | null>;
}

declare global {
  interface Window {
    screenwire?: ScreenwireBridge;
    __chatDraft?: string | null;
  }
}

export {};
