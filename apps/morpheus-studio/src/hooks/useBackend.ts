// Backend Integration Hooks for Morpheus
// Provides React hooks for all backend operations

import { useState, useEffect, useCallback } from 'react';
import { useMorpheusStore } from '../store';
import API, { wsService } from '../services/api';
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
} from '../types';

// ============================================================================
// useProjects - Project management hook
// ============================================================================

export function useProjects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchProjects = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await API.projects.list();
      setProjects(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch projects');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const createProject = useCallback(async (name: string, description: string) => {
    setIsLoading(true);
    try {
      const project = await API.projects.create({ name, description });
      setProjects((prev) => [project, ...prev]);
      return project;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create project');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const deleteProject = useCallback(async (id: string) => {
    setIsLoading(true);
    try {
      await API.projects.delete(id);
      setProjects((prev) => prev.filter((p) => p.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete project');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const duplicateProject = useCallback(async (id: string) => {
    setIsLoading(true);
    try {
      const project = await API.projects.duplicate(id);
      setProjects((prev) => [project, ...prev]);
      return project;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to duplicate project');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  return {
    projects,
    isLoading,
    error,
    fetchProjects,
    createProject,
    deleteProject,
    duplicateProject,
  };
}

// ============================================================================
// useCreativeConcept - Creative concept management
// ============================================================================

export function useCreativeConcept(projectId: string | null) {
  const [concept, setConcept] = useState<CreativeConcept | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchConcept = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const data = await API.concept.get(projectId);
      setConcept(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch concept');
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const setCreativeConcept = useCallback(async (data: Parameters<typeof API.concept.set>[1]) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const result = await API.concept.set(projectId, data);
      setConcept(result);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to set concept');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const uploadSourceFile = useCallback(async (file: File) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const result = await API.concept.uploadFile(projectId, file);
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload file');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchConcept();
  }, [fetchConcept]);

  return {
    concept,
    isLoading,
    error,
    fetchConcept,
    setCreativeConcept,
    uploadSourceFile,
  };
}

// ============================================================================
// useSkeleton - Skeleton plan management
// ============================================================================

export function useSkeleton(projectId: string | null) {
  const [skeleton, setSkeleton] = useState<SkeletonPlan | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchSkeleton = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const data = await API.skeleton.get(projectId);
      setSkeleton(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch skeleton');
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const generateSkeleton = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const job = await API.skeleton.generate(projectId);
      return job;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate skeleton');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const approveSkeleton = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      await API.skeleton.approve(projectId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to approve skeleton');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const requestEdit = useCallback(async (feedback: string) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const data = await API.skeleton.requestEdit(projectId, feedback);
      setSkeleton(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to request edit');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const updateScene = useCallback(async (sceneId: string, updates: Parameters<typeof API.skeleton.updateScene>[2]) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const data = await API.skeleton.updateScene(projectId, sceneId, updates);
      setSkeleton(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update scene');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchSkeleton();
  }, [fetchSkeleton]);

  return {
    skeleton,
    isLoading,
    error,
    fetchSkeleton,
    generateSkeleton,
    approveSkeleton,
    requestEdit,
    updateScene,
  };
}

// ============================================================================
// useEntities - Entity management (Cast, Locations, Props)
// ============================================================================

export function useEntities(projectId: string | null) {
  const [entities, setEntities] = useState<Entity[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchEntities = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const data = await API.entities.list(projectId);
      setEntities(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch entities');
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const createEntity = useCallback(async (data: Omit<Entity, 'id' | 'status'>) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const entity = await API.entities.create(projectId, data);
      setEntities((prev) => [...prev, entity]);
      return entity;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create entity');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const updateEntity = useCallback(async (entityId: string, updates: Partial<Entity>) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const entity = await API.entities.update(projectId, entityId, updates);
      setEntities((prev) => prev.map((e) => (e.id === entityId ? entity : e)));
      return entity;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update entity');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const deleteEntity = useCallback(async (entityId: string) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      await API.entities.delete(projectId, entityId);
      setEntities((prev) => prev.filter((e) => e.id !== entityId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete entity');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const uploadImage = useCallback(async (entityId: string, file: File) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const result = await API.entities.uploadImage(projectId, entityId, file);
      setEntities((prev) =>
        prev.map((e) =>
          e.id === entityId ? { ...e, imageUrl: result.imageUrl, status: 'complete' } : e
        )
      );
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload image');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const generateImage = useCallback(async (entityId: string) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const result = await API.entities.generateImage(projectId, entityId);
      setEntities((prev) =>
        prev.map((e) =>
          e.id === entityId ? { ...e, imageUrl: result.imageUrl, status: 'complete' } : e
        )
      );
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate image');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const generateAllImages = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      await API.entities.generateAll(projectId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate images');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchEntities();
  }, [fetchEntities]);

  return {
    entities,
    isLoading,
    error,
    fetchEntities,
    createEntity,
    updateEntity,
    deleteEntity,
    uploadImage,
    generateImage,
    generateAllImages,
  };
}

// ============================================================================
// useStoryboard - Storyboard management
// ============================================================================

export function useStoryboard(projectId: string | null) {
  const [frames, setFrames] = useState<StoryboardFrame[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStoryboard = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const data = await API.storyboard.get(projectId);
      setFrames(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch storyboard');
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const generateStoryboard = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const data = await API.storyboard.generate(projectId);
      setFrames(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate storyboard');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const approveStoryboard = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      await API.storyboard.approve(projectId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to approve storyboard');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const updateFrame = useCallback(async (frameId: string, updates: Parameters<typeof API.storyboard.updateFrame>[2]) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const frame = await API.storyboard.updateFrame(projectId, frameId, updates);
      setFrames((prev) => prev.map((f) => (f.id === frameId ? frame : f)));
      return frame;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update frame');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const regenerateFrame = useCallback(async (frameId: string) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const frame = await API.storyboard.regenerateFrame(projectId, frameId);
      setFrames((prev) => prev.map((f) => (f.id === frameId ? frame : f)));
      return frame;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to regenerate frame');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const uploadFrame = useCallback(async (frameId: string, file: File) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const result = await API.storyboard.uploadFrame(projectId, frameId, file);
      setFrames((prev) =>
        prev.map((f) =>
          f.id === frameId ? { ...f, imageUrl: result.imageUrl } : f
        )
      );
      return result;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload frame');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchStoryboard();
  }, [fetchStoryboard]);

  return {
    frames,
    isLoading,
    error,
    fetchStoryboard,
    generateStoryboard,
    approveStoryboard,
    updateFrame,
    regenerateFrame,
    uploadFrame,
  };
}

// ============================================================================
// useTimeline - Timeline management
// ============================================================================

export function useTimeline(projectId: string | null) {
  const [frames, setFrames] = useState<TimelineFrame[]>([]);
  const [dialogue, setDialogue] = useState<DialogueBlock[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTimeline = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const [framesData, dialogueData] = await Promise.all([
        API.timeline.getFrames(projectId),
        API.timeline.getDialogue(projectId),
      ]);
      setFrames(framesData);
      setDialogue(dialogueData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch timeline');
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const generateFrames = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const data = await API.timeline.generate(projectId);
      setFrames(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate frames');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const approveTimeline = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      await API.timeline.approve(projectId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to approve timeline');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const updateFrame = useCallback(async (frameId: string, updates: Parameters<typeof API.timeline.updateFrame>[2]) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const frame = await API.timeline.updateFrame(projectId, frameId, updates);
      setFrames((prev) => prev.map((f) => (f.id === frameId ? frame : f)));
      return frame;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update frame');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const regenerateFrame = useCallback(async (frameId: string) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const frame = await API.timeline.regenerateFrame(projectId, frameId);
      setFrames((prev) => prev.map((f) => (f.id === frameId ? frame : f)));
      return frame;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to regenerate frame');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const removeFrame = useCallback(async (frameId: string) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      await API.timeline.removeFrame(projectId, frameId);
      setFrames((prev) => prev.filter((f) => f.id !== frameId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to remove frame');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const expandFrame = useCallback(async (frameId: string, direction: 'before' | 'after') => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const newFrames = await API.timeline.expandFrame(projectId, frameId, direction);
      setFrames((prev) => [...prev, ...newFrames]);
      return newFrames;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to expand frame');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const updateDialogue = useCallback(async (dialogueId: string, updates: Parameters<typeof API.timeline.updateDialogue>[2]) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const block = await API.timeline.updateDialogue(projectId, dialogueId, updates);
      setDialogue((prev) => prev.map((d) => (d.id === dialogueId ? block : d)));
      return block;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update dialogue');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchTimeline();
  }, [fetchTimeline]);

  return {
    frames,
    dialogue,
    isLoading,
    error,
    fetchTimeline,
    generateFrames,
    approveTimeline,
    updateFrame,
    regenerateFrame,
    removeFrame,
    expandFrame,
    updateDialogue,
  };
}

// ============================================================================
// useVideo - Video generation
// ============================================================================

export function useVideo(projectId: string | null) {
  const [exports, setExports] = useState<VideoExport[]>([]);
  const [activeJob, setActiveJob] = useState<string | null>(null);
  const [jobProgress, setJobProgress] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchExports = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const data = await API.video.getExports(projectId);
      setExports(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch exports');
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const generateVideo = useCallback(async (options: Parameters<typeof API.video.generate>[1] = {}) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const { jobId } = await API.video.generate(projectId, options);
      setActiveJob(jobId);
      setJobProgress(0);
      return jobId;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start generation');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const checkJobStatus = useCallback(async (jobId: string) => {
    try {
      const status = await API.video.getJobStatus(jobId);
      setJobProgress(status.progress);
      if (status.status === 'completed') {
        setActiveJob(null);
        fetchExports();
      }
      return status;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to check status');
      throw err;
    }
  }, [fetchExports]);

  const deleteExport = useCallback(async (exportId: string) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      await API.video.deleteExport(projectId, exportId);
      setExports((prev) => prev.filter((e) => e.id !== exportId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete export');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchExports();
  }, [fetchExports]);

  return {
    exports,
    activeJob,
    jobProgress,
    isLoading,
    error,
    fetchExports,
    generateVideo,
    checkJobStatus,
    deleteExport,
  };
}

// ============================================================================
// useAgentChat - Agent chat integration
// ============================================================================

export function useAgentChat(projectId: string | null) {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [isTyping, setIsTyping] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchHistory = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const data = await API.chat.getHistory(projectId);
      setMessages(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch history');
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const sendMessage = useCallback(async (
    content: string,
    focusTarget?: { type: 'storyboard' | 'entity' | 'scene' | 'frame'; id: string; name: string }
  ) => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const { response } = await API.chat.sendMessage(projectId, { content, focusTarget });
      setMessages((prev) => [...prev, response]);
      return response;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send message');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const setFocus = useCallback(async (focus: { type: string; id: string; name: string } | null) => {
    if (!projectId) return;
    try {
      await API.chat.setFocus(projectId, focus);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to set focus');
    }
  }, [projectId]);

  const clearChat = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      await API.chat.clear(projectId);
      setMessages([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to clear chat');
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  return {
    messages,
    isTyping,
    isLoading,
    error,
    fetchHistory,
    sendMessage,
    setFocus,
    clearChat,
    setIsTyping,
  };
}

// ============================================================================
// useWorkers - Worker status monitoring
// ============================================================================

export function useWorkers(projectId: string | null) {
  const [workers, setWorkers] = useState<WorkerStatus[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchWorkers = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      const data = await API.workers.getStatus(projectId);
      setWorkers(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch workers');
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  const cancelWorker = useCallback(async (workerId: string) => {
    setIsLoading(true);
    try {
      await API.workers.cancel(workerId);
      setWorkers((prev) => prev.filter((w) => w.id !== workerId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to cancel worker');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const cancelAllWorkers = useCallback(async () => {
    if (!projectId) return;
    setIsLoading(true);
    try {
      await API.workers.cancelAll(projectId);
      setWorkers([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to cancel workers');
      throw err;
    } finally {
      setIsLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchWorkers();
  }, [fetchWorkers]);

  return {
    workers,
    isLoading,
    error,
    fetchWorkers,
    cancelWorker,
    cancelAllWorkers,
    setWorkers,
  };
}

// ============================================================================
// useWebSocket - Real-time WebSocket connection
// ============================================================================

export function useWebSocket(projectId: string | null) {
  const [socketConnected, setSocketConnected] = useState(false);
  const [workers, setWorkers] = useState<WorkerStatus[]>([]);
  const [videoProgress, setVideoProgress] = useState<{ jobId: string; progress: number } | null>(null);

  useEffect(() => {
    if (!projectId) {
      wsService.disconnect();
      return;
    }

    wsService.connect(projectId);

    const unsubConnected = wsService.on('connected', () => setSocketConnected(true));
    const unsubDisconnected = wsService.on('disconnected', () => setSocketConnected(false));

    const unsubWorkerUpdate = wsService.on<WorkerStatus>('worker_update', (worker) => {
      setWorkers((prev) => {
        const index = prev.findIndex((w) => w.id === worker.id);
        if (index >= 0) {
          const updated = [...prev];
          updated[index] = worker;
          return updated;
        }
        return [...prev, worker];
      });
    });

    const unsubWorkerComplete = wsService.on<{ workerId: string }>('worker_complete', ({ workerId }) => {
      setWorkers((prev) => prev.filter((w) => w.id !== workerId));
    });

    const unsubVideoProgress = wsService.on<{ jobId: string; progress: number }>('video_progress', (data) => {
      setVideoProgress(data);
    });

    const unsubVideoComplete = wsService.on<{ jobId: string }>('video_complete', ({ jobId }) => {
      if (videoProgress?.jobId === jobId) {
        setVideoProgress(null);
      }
    });

    const unsubAgentTyping = wsService.on<{ isTyping: boolean }>('agent_typing', (_data) => {
      void _data; // Can be used to show typing indicator
    });

    const unsubAgentMessage = wsService.on<AgentMessage>('agent_message', (_msg) => {
      void _msg; // Handle incoming agent messages
    });

    return () => {
      unsubConnected();
      unsubDisconnected();
      unsubWorkerUpdate();
      unsubWorkerComplete();
      unsubVideoProgress();
      unsubVideoComplete();
      unsubAgentTyping();
      unsubAgentMessage();
    };
  }, [projectId, videoProgress?.jobId]);

  return {
    isConnected: Boolean(projectId) && socketConnected,
    workers,
    videoProgress,
  };
}

// ============================================================================
// useProjectSync - Full project synchronization with backend
// ============================================================================

export function useProjectSync(projectId: string | null) {
  const store = useMorpheusStore();
  const { isConnected, workers: wsWorkers } = useWebSocket(projectId);

  const projects = useProjects();
  const concept = useCreativeConcept(projectId);
  const skeleton = useSkeleton(projectId);
  const entities = useEntities(projectId);
  const storyboard = useStoryboard(projectId);
  const timeline = useTimeline(projectId);
  const video = useVideo(projectId);
  const chat = useAgentChat(projectId);
  const workers = useWorkers(projectId);

  // Sync WebSocket workers with hook workers
  useEffect(() => {
    if (wsWorkers.length > 0) {
      workers.setWorkers(wsWorkers);
    }
  }, [wsWorkers, workers]);

  // Update store when data changes
  useEffect(() => {
    if (concept.concept) {
      store.setCreativeConcept(concept.concept);
    }
  }, [concept.concept, store]);

  useEffect(() => {
    if (skeleton.skeleton) {
      store.setSkeletonPlan(skeleton.skeleton);
    }
  }, [skeleton.skeleton, store]);

  useEffect(() => {
    if (entities.entities.length > 0) {
      // Sync entities to store
    }
  }, [entities.entities]);

  useEffect(() => {
    if (storyboard.frames.length > 0) {
      store.setStoryboardFrames(storyboard.frames);
    }
  }, [storyboard.frames, store]);

  useEffect(() => {
    if (timeline.frames.length > 0) {
      // Sync timeline to store
    }
  }, [timeline.frames]);

  return {
    isConnected,
    projects,
    concept,
    skeleton,
    entities,
    storyboard,
    timeline,
    video,
    chat,
    workers,
  };
}
