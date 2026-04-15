// API Service Layer for Morpheus Backend Integration
// Exposes all I/O paths for backend wiring

import type {
  Project,
  CreativeConcept,
  SkeletonPlan,
  Entity,
  StoryboardFrame,
  TimelineFrame,
  DialogueBlock,
  WorkerStatus,
  VideoExport,
  AgentMessage,
  CreativityLevel,
  WorkspaceSnapshot,
  WorkflowState,
  FrameContext,
} from '../types';

// API Configuration
const runtimeHost =
  typeof window !== 'undefined' && window.location.hostname
    ? window.location.hostname
    : 'localhost';
const browserOrigin = typeof window !== 'undefined' ? window.location.origin : '';
const browserHost = typeof window !== 'undefined' ? window.location.host : '';
const isBrowserLocalhost =
  typeof window !== 'undefined' &&
  ['localhost', '127.0.0.1', '0.0.0.0'].includes(window.location.hostname);
let apiBaseUrl =
  import.meta.env.VITE_API_URL ||
  (browserOrigin && !isBrowserLocalhost ? browserOrigin : `http://${runtimeHost}:8000`);
let wsBaseUrl =
  import.meta.env.VITE_WS_URL ||
  (browserHost && !isBrowserLocalhost
    ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${browserHost}/ws`
    : `ws://${runtimeHost}:8000/ws`);
const preloadedImageUrls = new Set<string>();
const preloadedVideoUrls = new Set<string>();

function schedulePreload(task: () => void): void {
  if (typeof window === 'undefined') {
    return;
  }

  const idleScheduler = (
    window as Window & {
      requestIdleCallback?: (callback: IdleRequestCallback) => number;
    }
  ).requestIdleCallback;

  if (typeof idleScheduler === 'function') {
    idleScheduler(() => task());
    return;
  }

  window.setTimeout(task, 0);
}

export function setApiBaseUrl(url: string): void {
  if (!url) return;
  apiBaseUrl = url.replace(/\/+$/, '');
}

export function setWsBaseUrl(url: string): void {
  if (!url) return;
  wsBaseUrl = url.replace(/\/+$/, '');
}

export function configureBackendBase(apiUrl: string): void {
  setApiBaseUrl(apiUrl);
  const derivedWs = apiUrl.replace(/^http/i, 'ws');
  setWsBaseUrl(`${derivedWs}/ws`);
}

// ============================================================================
// HTTP Client
// ============================================================================

async function fetchApi<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${apiBaseUrl}${endpoint}`;
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData;
  const headers = new Headers(options.headers || {});
  if (!isFormData && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ message: 'Unknown error' }));
    throw new Error(error.message || `HTTP ${response.status}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return response.json();
  }

  return (await response.text()) as T;
}

function normalizeProject(project: Project): Project {
  return {
    ...project,
    createdAt: new Date(project.createdAt),
    updatedAt: new Date(project.updatedAt),
    coverImageUrl: normalizeAssetUrl(project.coverImageUrl),
  };
}

function normalizeAssetUrl(url: string | null | undefined): string | undefined {
  if (!url) {
    return undefined;
  }
  if (/^(?:https?:)?\/\//i.test(url) || url.startsWith('data:') || url.startsWith('blob:')) {
    return url;
  }
  if (url.startsWith('/')) {
    return `${apiBaseUrl}${url}`;
  }
  return url;
}

function buildThumbnailUrl(
  sourceUrl: string | null | undefined,
  width: number,
  height: number,
  fit: 'cover' | 'contain' | 'inside' = 'cover',
): string | undefined {
  const normalized = normalizeAssetUrl(sourceUrl);
  if (!normalized) {
    return undefined;
  }
  if (normalized.startsWith('data:') || normalized.startsWith('blob:')) {
    return normalized;
  }

  try {
    const url = new URL(normalized, apiBaseUrl);
    const projectScoped = url.pathname.match(/^\/api\/projects\/([^/]+)\/file\/(.+)$/);
    const legacyScoped = url.pathname.match(/^\/api\/project\/file\/(.+)$/);

    if (projectScoped) {
      const [, projectId, requestedPath] = projectScoped;
      const thumb = new URL(`${apiBaseUrl}/api/projects/${projectId}/thumbnail/${requestedPath}`);
      const version = url.searchParams.get('v');
      if (version) {
        thumb.searchParams.set('v', version);
      }
      thumb.searchParams.set('w', String(width));
      thumb.searchParams.set('h', String(height));
      thumb.searchParams.set('fit', fit);
      thumb.searchParams.set('format', 'webp');
      return thumb.toString();
    }

    if (legacyScoped) {
      const [, requestedPath] = legacyScoped;
      const thumb = new URL(`${apiBaseUrl}/api/project/thumbnail/${requestedPath}`);
      const version = url.searchParams.get('v');
      if (version) {
        thumb.searchParams.set('v', version);
      }
      thumb.searchParams.set('w', String(width));
      thumb.searchParams.set('h', String(height));
      thumb.searchParams.set('fit', fit);
      thumb.searchParams.set('format', 'webp');
      return thumb.toString();
    }
  } catch {
    return normalized;
  }

  return normalized;
}

function normalizeEntity(entity: Entity): Entity {
  const imageUrl = normalizeAssetUrl(entity.imageUrl);
  const isLocation = entity.type === 'location';
  const fit = entity.type === 'cast' ? 'contain' : 'cover';
  return {
    ...entity,
    imageUrl,
    thumbnailUrl: buildThumbnailUrl(
      imageUrl,
      isLocation ? 720 : 480,
      isLocation ? 405 : 480,
      fit,
    ),
  };
}

function normalizeStoryboardFrame(frame: StoryboardFrame): StoryboardFrame {
  const imageUrl = normalizeAssetUrl(frame.imageUrl);
  return {
    ...frame,
    imageUrl,
    thumbnailUrl: buildThumbnailUrl(imageUrl, 640, 360),
  };
}

function normalizeTimelineFrame(frame: TimelineFrame): TimelineFrame {
  const imageUrl = normalizeAssetUrl(frame.imageUrl);
  const videoUrl = normalizeAssetUrl(frame.videoUrl);
  return {
    ...frame,
    imageUrl,
    videoUrl,
    thumbnailUrl: buildThumbnailUrl(imageUrl, 360, 208),
  };
}

export function preloadImageAsset(url: string | null | undefined): void {
  const normalized = normalizeAssetUrl(url);
  if (!normalized || typeof window === 'undefined' || normalized.startsWith('data:') || normalized.startsWith('blob:')) {
    return;
  }
  if (preloadedImageUrls.has(normalized)) {
    return;
  }
  preloadedImageUrls.add(normalized);
  schedulePreload(() => {
    const img = new window.Image();
    img.decoding = 'async';
    img.src = normalized;
  });
}

export function preloadVideoAsset(url: string | null | undefined, posterUrl?: string | null): void {
  const normalized = normalizeAssetUrl(url);
  if (!normalized || typeof window === 'undefined' || normalized.startsWith('data:') || normalized.startsWith('blob:')) {
    return;
  }
  if (preloadedVideoUrls.has(normalized)) {
    return;
  }
  preloadedVideoUrls.add(normalized);
  schedulePreload(() => {
    const video = document.createElement('video');
    video.preload = 'metadata';
    video.muted = true;
    video.playsInline = true;
    const poster = normalizeAssetUrl(posterUrl);
    if (poster) {
      video.poster = poster;
      preloadImageAsset(poster);
    }
    video.src = normalized;
    video.load();
  });
}

export function preloadMediaWindow(
  items: Array<{
    imageUrl?: string | null;
    videoUrl?: string | null;
    posterUrl?: string | null;
  }>,
): void {
  items.forEach((item) => {
    preloadImageAsset(item.imageUrl || item.posterUrl);
    if (item.videoUrl) {
      preloadVideoAsset(item.videoUrl, item.posterUrl || item.imageUrl);
    }
  });
}

function normalizeAgentMessage(message: AgentMessage): AgentMessage {
  return {
    ...message,
    timestamp: new Date(message.timestamp),
  };
}

function normalizeWorkspace(snapshot: WorkspaceSnapshot): WorkspaceSnapshot {
  return {
    ...snapshot,
    project: normalizeProject(snapshot.project),
    entities: (snapshot.entities || []).map(normalizeEntity),
    storyboardFrames: (snapshot.storyboardFrames || []).map(normalizeStoryboardFrame),
    timelineFrames: (snapshot.timelineFrames || []).map(normalizeTimelineFrame),
    messages: (snapshot.messages || []).map(normalizeAgentMessage),
    reports: snapshot.reports
      ? Object.fromEntries(
          Object.entries(snapshot.reports).map(([key, value]) => [key, normalizeAssetUrl(value)])
        )
      : snapshot.reports,
  };
}

// ============================================================================
// Projects API
// ============================================================================

export const ProjectsAPI = {
  // GET /api/projects - List all projects
  list: (): Promise<Project[]> =>
    fetchApi('/api/projects'),

  // GET /api/projects/:id - Get project by ID
  get: (id: string): Promise<Project> =>
    fetchApi(`/api/projects/${id}`),

  // POST /api/projects - Create new project
  create: (data: { name: string; description: string }): Promise<Project> =>
    fetchApi('/api/projects', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // PUT /api/projects/:id - Update project
  update: (id: string, data: Partial<Project>): Promise<Project> =>
    fetchApi(`/api/projects/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  // DELETE /api/projects/:id - Delete project
  delete: (id: string): Promise<void> =>
    fetchApi(`/api/projects/${id}`, { method: 'DELETE' }),

  // POST /api/projects/:id/duplicate - Duplicate project
  duplicate: (id: string): Promise<Project> =>
    fetchApi(`/api/projects/${id}/duplicate`, { method: 'POST' }),
};

export const WorkspaceAPI = {
  get: async (projectId: string): Promise<WorkspaceSnapshot> =>
    normalizeWorkspace(await fetchApi(`/api/projects/${projectId}/workspace`)),
  diagnostics: (projectId: string): Promise<Record<string, unknown>> =>
    fetchApi(`/api/projects/${projectId}/diagnostics`),
  greenlight: (projectId: string): Promise<Record<string, unknown>> =>
    fetchApi(`/api/projects/${projectId}/greenlight`),
};

// ============================================================================
// Creative Concept API
// ============================================================================

export const CreativeConceptAPI = {
  // GET /api/projects/:id/concept - Get creative concept
  get: (projectId: string): Promise<CreativeConcept> =>
    fetchApi(`/api/projects/${projectId}/concept`),

  // POST /api/projects/:id/concept - Set creative concept
  set: (projectId: string, data: {
    sourceText: string;
    mediaStyle: string;
    frameCount: number | string;
    creativityLevel: CreativityLevel;
  }): Promise<CreativeConcept> =>
    fetchApi(`/api/projects/${projectId}/concept`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // POST /api/projects/:id/concept/upload - Upload source file
  uploadFile: (projectId: string, file: File): Promise<{ text: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    return fetchApi(`/api/projects/${projectId}/concept/upload`, {
      method: 'POST',
      body: formData,
      headers: {}, // Let browser set content-type for FormData
    });
  },
};

// ============================================================================
// Skeleton Plan API
// ============================================================================

export const SkeletonAPI = {
  // GET /api/projects/:id/skeleton - Get skeleton plan
  get: (projectId: string): Promise<SkeletonPlan> =>
    fetchApi(`/api/projects/${projectId}/skeleton`),

  // POST /api/projects/:id/skeleton/generate - Generate skeleton from concept
  generate: (projectId: string): Promise<{ jobId: string; status: string; message: string }> =>
    fetchApi(`/api/projects/${projectId}/skeleton/generate`, { method: 'POST' }),

  // POST /api/projects/:id/skeleton/approve - Approve skeleton
  approve: (projectId: string): Promise<void> =>
    fetchApi(`/api/projects/${projectId}/skeleton/approve`, { method: 'POST' }),

  // POST /api/projects/:id/skeleton/edit-request - Request edits
  requestEdit: (projectId: string, feedback: string): Promise<SkeletonPlan> =>
    fetchApi(`/api/projects/${projectId}/skeleton/edit-request`, {
      method: 'POST',
      body: JSON.stringify({ feedback }),
    }),

  // PUT /api/projects/:id/skeleton/scenes/:sceneId - Update scene
  updateScene: (projectId: string, sceneId: string, data: {
    description?: string;
    location?: string;
    characters?: string[];
  }): Promise<SkeletonPlan> =>
    fetchApi(`/api/projects/${projectId}/skeleton/scenes/${sceneId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
};

export const WorkflowAPI = {
  approve: (projectId: string, gate: 'skeleton' | 'references' | 'timeline' | 'video'): Promise<WorkspaceSnapshot> =>
    fetchApi<WorkspaceSnapshot>(`/api/projects/${projectId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ gate }),
    }).then(normalizeWorkspace),

  requestChanges: (projectId: string, gate: 'skeleton' | 'references' | 'timeline' | 'video', feedback: string): Promise<{ ok: boolean; changeRequests: WorkflowState['changeRequests'] }> =>
    fetchApi(`/api/projects/${projectId}/request-changes`, {
      method: 'POST',
      body: JSON.stringify({ gate, feedback }),
    }),
};

// ============================================================================
// Entities API (Cast, Locations, Props)
// ============================================================================

export const EntitiesAPI = {
  // GET /api/projects/:id/entities - List all entities
  list: async (projectId: string): Promise<Entity[]> =>
    (await fetchApi<Entity[]>(`/api/projects/${projectId}/entities`)).map(normalizeEntity),

  // GET /api/projects/:id/entities/:entityId - Get entity
  get: async (projectId: string, entityId: string): Promise<Entity> =>
    normalizeEntity(await fetchApi<Entity>(`/api/projects/${projectId}/entities/${entityId}`)),

  // POST /api/projects/:id/entities - Create entity
  create: (projectId: string, data: Omit<Entity, 'id' | 'status'>): Promise<Entity> =>
    fetchApi<Entity>(`/api/projects/${projectId}/entities`, {
      method: 'POST',
      body: JSON.stringify(data),
    }).then(normalizeEntity),

  // PUT /api/projects/:id/entities/:entityId - Update entity
  update: (projectId: string, entityId: string, data: Partial<Entity>): Promise<Entity> =>
    fetchApi<Entity>(`/api/projects/${projectId}/entities/${entityId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }).then(normalizeEntity),

  // DELETE /api/projects/:id/entities/:entityId - Delete entity
  delete: (projectId: string, entityId: string): Promise<void> =>
    fetchApi(`/api/projects/${projectId}/entities/${entityId}`, { method: 'DELETE' }),

  // POST /api/projects/:id/entities/:entityId/upload - Upload entity image
  uploadImage: (projectId: string, entityId: string, file: File): Promise<{ imageUrl: string }> => {
    const formData = new FormData();
    formData.append('image', file);
    return fetchApi<{ imageUrl: string }>(`/api/projects/${projectId}/entities/${entityId}/upload`, {
      method: 'POST',
      body: formData,
      headers: {},
    }).then((result) => ({ imageUrl: normalizeAssetUrl(result.imageUrl) || result.imageUrl }));
  },

  // POST /api/projects/:id/entities/:entityId/generate - Generate AI image
  generateImage: (projectId: string, entityId: string): Promise<{ imageUrl: string }> =>
    fetchApi(`/api/projects/${projectId}/entities/${entityId}/generate`, { method: 'POST' }),

  // POST /api/projects/:id/entities/generate-all - Generate all entity images
  generateAll: (projectId: string): Promise<void> =>
    fetchApi(`/api/projects/${projectId}/entities/generate-all`, { method: 'POST' }),
};

export const GraphAPI = {
  getNode: (projectId: string, nodeType: 'cast' | 'location' | 'prop' | 'scene' | 'frame' | 'cast_frame_state' | 'prop_frame_state' | 'location_frame_state', nodeId: string): Promise<Record<string, unknown>> =>
    fetchApi(`/api/projects/${projectId}/graph/${nodeType}/${nodeId}`),

  updateNode: (projectId: string, nodeType: 'cast' | 'location' | 'prop' | 'scene' | 'frame' | 'cast_frame_state' | 'prop_frame_state' | 'location_frame_state', nodeId: string, updates: Record<string, unknown>): Promise<WorkspaceSnapshot> =>
    fetchApi<WorkspaceSnapshot>(`/api/projects/${projectId}/graph/${nodeType}/${nodeId}`, {
      method: 'PATCH',
      body: JSON.stringify({ updates }),
    }).then(normalizeWorkspace),

  getFrameContext: (projectId: string, frameId: string): Promise<FrameContext> =>
    fetchApi(`/api/projects/${projectId}/graph/frame/${frameId}/context`),
};

// ============================================================================
// Storyboard API
// ============================================================================

export const StoryboardAPI = {
  // GET /api/projects/:id/storyboard - Get storyboard frames
  get: async (projectId: string): Promise<StoryboardFrame[]> =>
    (await fetchApi<StoryboardFrame[]>(`/api/projects/${projectId}/storyboard`)).map(normalizeStoryboardFrame),

  // POST /api/projects/:id/storyboard/generate - Generate storyboard from skeleton
  generate: (projectId: string): Promise<StoryboardFrame[]> =>
    fetchApi<StoryboardFrame[]>(`/api/projects/${projectId}/storyboard/generate`, { method: 'POST' }).then((frames) => frames.map(normalizeStoryboardFrame)),

  // POST /api/projects/:id/storyboard/approve - Approve storyboard
  approve: (projectId: string): Promise<void> =>
    fetchApi(`/api/projects/${projectId}/storyboard/approve`, { method: 'POST' }),

  // PUT /api/projects/:id/storyboard/:frameId - Update frame
  updateFrame: (projectId: string, frameId: string, data: {
    description?: string;
    shotType?: string;
  }): Promise<StoryboardFrame> =>
    fetchApi<StoryboardFrame>(`/api/projects/${projectId}/storyboard/${frameId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }).then(normalizeStoryboardFrame),

  // POST /api/projects/:id/storyboard/:frameId/regenerate - Regenerate frame
  regenerateFrame: (projectId: string, frameId: string): Promise<StoryboardFrame> =>
    fetchApi<StoryboardFrame>(`/api/projects/${projectId}/storyboard/${frameId}/regenerate`, { method: 'POST' }).then(normalizeStoryboardFrame),

  // POST /api/projects/:id/storyboard/:frameId/upload - Upload custom storyboard
  uploadFrame: (projectId: string, frameId: string, file: File): Promise<{ imageUrl: string }> => {
    const formData = new FormData();
    formData.append('image', file);
    return fetchApi<{ imageUrl: string }>(`/api/projects/${projectId}/storyboard/${frameId}/upload`, {
      method: 'POST',
      body: formData,
      headers: {},
    }).then((result) => ({ imageUrl: normalizeAssetUrl(result.imageUrl) || result.imageUrl }));
  },
};

// ============================================================================
// Timeline API
// ============================================================================

export const TimelineAPI = {
  // GET /api/projects/:id/timeline - Get timeline frames
  getFrames: async (projectId: string): Promise<TimelineFrame[]> =>
    (await fetchApi<TimelineFrame[]>(`/api/projects/${projectId}/timeline`)).map(normalizeTimelineFrame),

  // GET /api/projects/:id/timeline/dialogue - Get dialogue blocks
  getDialogue: (projectId: string): Promise<DialogueBlock[]> =>
    fetchApi(`/api/projects/${projectId}/timeline/dialogue`),

  // POST /api/projects/:id/timeline/generate - Generate frames from storyboard
  generate: (projectId: string): Promise<TimelineFrame[]> =>
    fetchApi<TimelineFrame[]>(`/api/projects/${projectId}/timeline/generate`, { method: 'POST' }).then((frames) => frames.map(normalizeTimelineFrame)),

  // POST /api/projects/:id/timeline/approve - Approve timeline
  approve: (projectId: string): Promise<void> =>
    fetchApi(`/api/projects/${projectId}/timeline/approve`, { method: 'POST' }),

  // PUT /api/projects/:id/timeline/:frameId - Update frame
  updateFrame: (projectId: string, frameId: string, data: {
    duration?: number;
    prompt?: string;
    dialogueId?: string;
    trimStart?: number;
    trimEnd?: number;
  }): Promise<TimelineFrame> =>
    fetchApi<TimelineFrame>(`/api/projects/${projectId}/timeline/${frameId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }).then(normalizeTimelineFrame),

  // POST /api/projects/:id/timeline/:frameId/upload - Upload custom frame image
  uploadFrame: (projectId: string, frameId: string, file: File): Promise<{ imageUrl: string }> => {
    const formData = new FormData();
    formData.append('image', file);
    return fetchApi<{ imageUrl: string }>(`/api/projects/${projectId}/timeline/${frameId}/upload`, {
      method: 'POST',
      body: formData,
    }).then((result) => ({ imageUrl: normalizeAssetUrl(result.imageUrl) || result.imageUrl }));
  },

  // POST /api/projects/:id/timeline/:frameId/regenerate - Regenerate frame
  regenerateFrame: (projectId: string, frameId: string): Promise<TimelineFrame> =>
    fetchApi<TimelineFrame>(`/api/projects/${projectId}/timeline/${frameId}/regenerate`, { method: 'POST' }).then(normalizeTimelineFrame),

  // POST /api/projects/:id/timeline/:frameId/edit - Edit frame image via nano-banana
  editFrame: (projectId: string, frameId: string, prompt: string): Promise<TimelineFrame> =>
    fetchApi<TimelineFrame>(`/api/projects/${projectId}/timeline/${frameId}/edit`, {
      method: 'POST',
      body: JSON.stringify({ prompt }),
    }).then(normalizeTimelineFrame),

  // DELETE /api/projects/:id/timeline/:frameId - Remove frame
  removeFrame: (projectId: string, frameId: string): Promise<void> =>
    fetchApi(`/api/projects/${projectId}/timeline/${frameId}`, { method: 'DELETE' }),

  // POST /api/projects/:id/timeline/:frameId/expand - Expand frame
  expandFrame: (projectId: string, frameId: string, direction: 'before' | 'after'): Promise<TimelineFrame[]> =>
    fetchApi<TimelineFrame[]>(`/api/projects/${projectId}/timeline/${frameId}/expand`, {
      method: 'POST',
      body: JSON.stringify({ direction }),
    }).then((frames) => frames.map(normalizeTimelineFrame)),

  // PUT /api/projects/:id/timeline/dialogue/:dialogueId - Update dialogue
  updateDialogue: (projectId: string, dialogueId: string, data: {
    text?: string;
    character?: string;
    startFrame?: number;
    endFrame?: number;
    duration?: number;
  }): Promise<DialogueBlock> =>
    fetchApi(`/api/projects/${projectId}/timeline/dialogue/${dialogueId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
};

// ============================================================================
// Video API
// ============================================================================

export const VideoAPI = {
  // POST /api/projects/:id/video/generate - Generate video from timeline
  generate: (projectId: string, options: {
    resolution?: '720p' | '1080p' | '4K';
    fps?: number;
    format?: 'mp4' | 'webm';
  } = {}): Promise<{ jobId: string }> =>
    fetchApi(`/api/projects/${projectId}/video/generate`, {
      method: 'POST',
      body: JSON.stringify(options),
    }),

  // GET /api/projects/:id/video/exports - Get video exports
  getExports: (projectId: string): Promise<VideoExport[]> =>
    fetchApi(`/api/projects/${projectId}/video/exports`),

  // GET /api/video/jobs/:jobId/status - Get generation status
  getJobStatus: (jobId: string): Promise<{
    status: 'pending' | 'processing' | 'completed' | 'failed';
    progress: number;
    url?: string;
    error?: string;
  }> =>
    fetchApi(`/api/video/jobs/${jobId}/status`),

  // DELETE /api/projects/:id/video/exports/:exportId - Delete export
  deleteExport: (projectId: string, exportId: string): Promise<void> =>
    fetchApi(`/api/projects/${projectId}/video/exports/${exportId}`, { method: 'DELETE' }),
};

// ============================================================================
// Agent Chat API
// ============================================================================

export const AgentChatAPI = {
  // GET /api/projects/:id/chat - Get chat history
  getHistory: async (projectId: string): Promise<AgentMessage[]> =>
    (await fetchApi<AgentMessage[]>(`/api/projects/${projectId}/chat`)).map(normalizeAgentMessage),

  // POST /api/projects/:id/chat - Send message to agent
  sendMessage: async (projectId: string, data: {
    content: string;
    mode?: 'suggest' | 'apply' | 'regenerate';
    focusTarget?: {
      type: 'storyboard' | 'entity' | 'scene' | 'frame' | 'cast' | 'location' | 'prop';
      id: string;
      name: string;
    };
    focusTargets?: Array<{
      type: string;
      id: string;
      name: string;
    }>;
  }): Promise<{ response: AgentMessage }> => {
    const payload = await fetchApi<{ response: AgentMessage }>(`/api/projects/${projectId}/chat`, {
      method: 'POST',
      body: JSON.stringify(data),
    });
    return { response: normalizeAgentMessage(payload.response) };
  },

  // POST /api/projects/:id/chat/focus - Set focus context
  setFocus: (projectId: string, focus: {
    type: string;
    id: string;
    name: string;
  } | null): Promise<void> =>
    fetchApi(`/api/projects/${projectId}/chat/focus`, {
      method: 'POST',
      body: JSON.stringify({ focus }),
    }),

  // DELETE /api/projects/:id/chat - Clear chat history
  clear: (projectId: string): Promise<void> =>
    fetchApi(`/api/projects/${projectId}/chat`, { method: 'DELETE' }),
};

// ============================================================================
// Workers API
// ============================================================================

export const WorkersAPI = {
  // GET /api/projects/:id/workers - Get worker statuses
  getStatus: (projectId: string): Promise<WorkerStatus[]> =>
    fetchApi(`/api/projects/${projectId}/workers`),

  // POST /api/projects/:id/workers/cancel - Cancel all workers
  cancelAll: (projectId: string): Promise<void> =>
    fetchApi(`/api/projects/${projectId}/workers/cancel`, { method: 'POST' }),

  // POST /api/workers/:workerId/cancel - Cancel specific worker
  cancel: (workerId: string): Promise<void> =>
    fetchApi(`/api/workers/${workerId}/cancel`, { method: 'POST' }),
};

// ============================================================================
// WebSocket Connection for Real-time Updates
// ============================================================================

export type WebSocketMessage =
  | { type: 'workspace_update'; data: WorkspaceSnapshot }
  | { type: 'worker_snapshot'; data: WorkerStatus[] }
  | { type: 'worker_update'; data: WorkerStatus }
  | { type: 'worker_complete'; data: { workerId: string; result: unknown } }
  | { type: 'frame_generated'; data: { frameId: string; imageUrl: string } }
  | { type: 'entity_image_generated'; data: { entityId: string; imageUrl: string } }
  | { type: 'storyboard_generated'; data: { frameId: string; imageUrl: string } }
  | { type: 'video_progress'; data: { jobId: string; progress: number } }
  | { type: 'video_complete'; data: { jobId: string; url: string } }
  | { type: 'agent_typing'; data: { isTyping: boolean } }
  | { type: 'agent_message'; data: AgentMessage }
  | { type: 'project_update'; data: Partial<Project> };

export class WebSocketService {
  private ws: WebSocket | null = null;
  private projectId: string | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000;
  private listeners: Map<string, Set<(data: unknown) => void>> = new Map();

  connect(projectId: string): void {
    this.projectId = projectId;
    this.reconnectAttempts = 0;
    this.doConnect();
  }

  private doConnect(): void {
    if (!this.projectId) return;

    const url = `${wsBaseUrl}/projects/${this.projectId}`;
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      console.log('[WebSocket] Connected');
      this.reconnectAttempts = 0;
      this.emit('connected', {});
    };

    this.ws.onmessage = (event) => {
      try {
        const message: WebSocketMessage = JSON.parse(event.data);
        if (message.type === 'workspace_update') {
          this.emit(message.type, normalizeWorkspace(message.data));
          return;
        }
        if (message.type === 'frame_generated' || message.type === 'entity_image_generated' || message.type === 'storyboard_generated') {
          this.emit(message.type, {
            ...message.data,
            imageUrl: normalizeAssetUrl(message.data.imageUrl) || message.data.imageUrl,
          });
          return;
        }
        this.emit(message.type, message.data);
      } catch (err) {
        console.error('[WebSocket] Failed to parse message:', err);
      }
    };

    this.ws.onclose = () => {
      console.log('[WebSocket] Disconnected');
      this.emit('disconnected', {});
      this.attemptReconnect();
    };

    this.ws.onerror = (error) => {
      console.error('[WebSocket] Error:', error);
      this.emit('error', error);
    };
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('[WebSocket] Max reconnect attempts reached');
      return;
    }

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);

    console.log(`[WebSocket] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

    setTimeout(() => this.doConnect(), delay);
  }

  disconnect(): void {
    this.ws?.close();
    this.ws = null;
    this.projectId = null;
  }

  on<T>(event: string, callback: (data: T) => void): () => void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(callback as (data: unknown) => void);

    return () => {
      this.listeners.get(event)?.delete(callback as (data: unknown) => void);
    };
  }

  private emit(event: string, data: unknown): void {
    this.listeners.get(event)?.forEach((callback) => callback(data));
  }

  send(message: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }
}

// Singleton instance
export const wsService = new WebSocketService();

// ============================================================================
// Export all APIs
// ============================================================================

export const API = {
  workspace: WorkspaceAPI,
  projects: ProjectsAPI,
  concept: CreativeConceptAPI,
  skeleton: SkeletonAPI,
  entities: EntitiesAPI,
  graph: GraphAPI,
  storyboard: StoryboardAPI,
  timeline: TimelineAPI,
  video: VideoAPI,
  chat: AgentChatAPI,
  workflow: WorkflowAPI,
  workers: WorkersAPI,
  ws: wsService,
};

export const backendConfig = {
  get apiBaseUrl(): string {
    return apiBaseUrl;
  },
  get wsBaseUrl(): string {
    return wsBaseUrl;
  },
};

export default API;
