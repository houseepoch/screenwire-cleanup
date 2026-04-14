import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type {
  Project,
  ProjectStatus,
  CreativeConcept,
  SkeletonPlan,
  Entity,
  StoryboardFrame,
  TimelineFrame,
  DialogueBlock,
  AgentMessage,
  WorkerStatus,
  CreativityLevel,
  GenerationMode,
  TabType,
  VideoExport,
  SelectedItem,
  WorkflowState,
} from '../types';

interface MorpheusState {
  // Navigation
  currentView: 'home' | 'project' | 'onboarding';
  setCurrentView: (view: 'home' | 'project' | 'onboarding') => void;

  // Projects
  projects: Project[];
  setProjects: (projects: Project[]) => void;
  currentProject: Project | null;
  createProject: (name: string, description: string) => Project;
  selectProject: (project: Project | null) => void;
  updateProjectStatus: (status: ProjectStatus) => void;
  updateProjectProgress: (progress: number) => void;

  // Creative Concept
  creativeConcept: CreativeConcept | null;
  setCreativeConcept: (concept: CreativeConcept) => void;

  // Creativity Level
  creativityLevel: CreativityLevel;
  setCreativityLevel: (level: CreativityLevel) => void;

  // Generation Mode
  generationMode: GenerationMode;
  setGenerationMode: (mode: GenerationMode) => void;

  // Skeleton Plan
  skeletonPlan: SkeletonPlan | null;
  setSkeletonPlan: (plan: SkeletonPlan) => void;
  scriptText: string;
  setScriptText: (script: string) => void;
  approveSkeleton: () => void;
  requestSkeletonEdit: (feedback: string) => void;

  // Entities
  entities: Entity[];
  addEntity: (entity: Entity) => void;
  updateEntity: (id: string, updates: Partial<Entity>) => void;
  uploadEntityImage: (id: string, imageUrl: string) => void;

  // Storyboard
  storyboardFrames: StoryboardFrame[];
  setStoryboardFrames: (frames: StoryboardFrame[]) => void;
  approveStoryboard: () => void;

  // Timeline
  timelineFrames: TimelineFrame[];
  dialogueBlocks: DialogueBlock[];
  selectedFrameId: string | null;
  setSelectedFrameId: (id: string | null) => void;
  regenerateFrame: (id: string) => void;
  removeFrame: (id: string) => void;
  expandFrame: (id: string, direction: 'before' | 'after') => void;
  updateFrameDuration: (id: string, duration: number) => void;
  approveTimeline: () => void;
  linkFrameToDialogue: (frameId: string, dialogueId: string) => void;
  unlinkFrameFromDialogue: (frameId: string) => void;
  distributeFrameDurations: (dialogueId: string) => void;

  // Workers
  workers: WorkerStatus[];
  setWorkers: (workers: WorkerStatus[]) => void;
  updateWorkerStatus: (id: string, status: Partial<WorkerStatus>) => void;

  // Agent Chat
  messages: AgentMessage[];
  setMessages: (messages: AgentMessage[]) => void;
  addMessage: (message: Omit<AgentMessage, 'id' | 'timestamp'>) => void;
  focusedItem: { type: string; id: string; name: string } | null;
  setFocusedItem: (item: { type: string; id: string; name: string } | null) => void;

  // Active Tab
  activeTab: TabType;
  setActiveTab: (tab: TabType) => void;

  // Video
  videoExports: VideoExport[];
  addVideoExport: (export_: VideoExport) => void;
  reports: {
    projectReport?: string | null;
    videoPromptProjection?: string | null;
    finalExport?: string | null;
    projectCover?: string | null;
    projectCoverSummary?: string | null;
    projectCoverMeta?: string | null;
    greenlightReport?: string | null;
    uiPhaseReport?: string | null;
  };
  workflow: WorkflowState;
  setWorkflow: (workflow: WorkflowState) => void;
  hydrateWorkspace: (snapshot: {
    project: Project;
    creativeConcept: CreativeConcept;
    skeletonPlan: SkeletonPlan;
    scriptText: string;
    entities: Entity[];
    storyboardFrames: StoryboardFrame[];
    timelineFrames: TimelineFrame[];
    dialogueBlocks: DialogueBlock[];
    workers?: WorkerStatus[];
    messages?: AgentMessage[];
    workflow?: WorkflowState;
    reports?: {
      projectReport?: string | null;
      videoPromptProjection?: string | null;
      finalExport?: string | null;
      projectCover?: string | null;
      projectCoverSummary?: string | null;
      projectCoverMeta?: string | null;
      greenlightReport?: string | null;
      uiPhaseReport?: string | null;
    };
  }) => void;

  // UI State
  isChatOpen: boolean;
  setIsChatOpen: (open: boolean) => void;
  isTimelineExpanded: boolean;
  setIsTimelineExpanded: (expanded: boolean) => void;
  isExportWizardOpen: boolean;
  setIsExportWizardOpen: (open: boolean) => void;
  mediaView: 'prompt' | 'image' | 'video';
  setMediaView: (view: 'prompt' | 'image' | 'video') => void;
  
  // Mobile View State
  mobileView: 'chat' | 'details';
  setMobileView: (view: 'chat' | 'details') => void;
  isTimelineTrayOpen: boolean;
  setIsTimelineTrayOpen: (open: boolean) => void;
  highlightedItem: { type: string; id: string; name: string } | null;
  setHighlightedItem: (item: { type: string; id: string; name: string } | null) => void;
  injectFocusToChat: (item: { type: string; id: string; name: string }) => void;
  
  // Multi-select State (Shift+Click)
  selectedItems: SelectedItem[];
  toggleItemSelection: (item: SelectedItem, isShiftClick: boolean) => void;
  clearSelection: () => void;
  isItemSelected: (id: string) => boolean;
}

type PersistedMorpheusState = Partial<MorpheusState>;

export const useMorpheusStore = create<MorpheusState>()(
  persist(
    (set, get) => ({
      // Navigation
      currentView: 'home',
      setCurrentView: (view) => set({ currentView: view }),

      // Projects
      projects: [],
      setProjects: (projects) => set({ projects }),
      currentProject: null,
      createProject: (name, description) => {
        const project: Project = {
          id: `proj-${Date.now()}`,
          name,
          description,
          status: 'onboarding',
          createdAt: new Date(),
          updatedAt: new Date(),
          creativityLevel: 'balanced',
          generationMode: 'assisted',
          progress: 0,
        };
        set((state) => ({
          projects: [project, ...state.projects],
          currentProject: project,
        }));
        return project;
      },
      selectProject: (project) => set({ currentProject: project }),
      updateProjectStatus: (status) => {
        const { currentProject } = get();
        if (!currentProject) return;
        set({
          currentProject: { ...currentProject, status },
          projects: get().projects.map((p) =>
            p.id === currentProject.id ? { ...p, status } : p
          ),
        });
      },
      updateProjectProgress: (progress) => {
        const { currentProject } = get();
        if (!currentProject) return;
        set({
          currentProject: { ...currentProject, progress },
          projects: get().projects.map((p) =>
            p.id === currentProject.id ? { ...p, progress } : p
          ),
        });
      },

      // Creative Concept
      creativeConcept: null,
      setCreativeConcept: (concept) => set({ creativeConcept: concept }),

      // Creativity Level
      creativityLevel: 'balanced',
      setCreativityLevel: (level) => set({ creativityLevel: level }),

      // Generation Mode
      generationMode: 'assisted',
      setGenerationMode: (mode) => set({ generationMode: mode }),

      // Skeleton Plan
      skeletonPlan: null,
      setSkeletonPlan: (plan) => set({ skeletonPlan: plan }),
      scriptText: '',
      setScriptText: (scriptText) => set({ scriptText }),
      approveSkeleton: () => {
        set((state) => {
          if (state.currentProject) {
            return {
              currentProject: { ...state.currentProject, status: 'generating_assets' },
              projects: state.projects.map((p) =>
                p.id === state.currentProject!.id
                  ? { ...p, status: 'generating_assets' }
                  : p
              ),
            };
          }
          return state;
        });
      },
      requestSkeletonEdit: (feedback) => {
        // In real implementation, this would send feedback to the agent
        console.log('Skeleton edit requested:', feedback);
      },

      // Entities
      entities: [],
      addEntity: (entity) =>
        set((state) => ({ entities: [...state.entities, entity] })),
      updateEntity: (id, updates) =>
        set((state) => ({
          entities: state.entities.map((e) =>
            e.id === id ? { ...e, ...updates } : e
          ),
        })),
      uploadEntityImage: (id, imageUrl) =>
        set((state) => ({
          entities: state.entities.map((e) =>
            e.id === id ? { ...e, imageUrl, status: 'complete' } : e
          ),
        })),

      // Storyboard
      storyboardFrames: [],
      setStoryboardFrames: (frames) => set({ storyboardFrames: frames }),
      approveStoryboard: () => {
        set((state) => ({
          storyboardFrames: state.storyboardFrames.map((f) =>
            f.status === 'complete' ? { ...f, status: 'approved' } : f
          ),
        }));
        get().updateProjectStatus('generating_frames');
      },

      // Timeline
      timelineFrames: [],
      dialogueBlocks: [],
      selectedFrameId: null,
      setSelectedFrameId: (id) => set({ selectedFrameId: id }),
      regenerateFrame: (id) => {
        console.log('Regenerating frame:', id);
        // In real implementation, this would trigger frame regeneration
      },
      removeFrame: (id) => {
        set((state) => ({
          timelineFrames: state.timelineFrames.filter((f) => f.id !== id),
        }));
      },
      expandFrame: (id, direction) => {
        console.log('Expanding frame:', id, direction);
        // In real implementation, this would add new frames
      },
      updateFrameDuration: (id, duration) => {
        set((state) => ({
          timelineFrames: state.timelineFrames.map((f) =>
            f.id === id ? { ...f, duration: Math.max(2, Math.min(15, duration)) } : f
          ),
        }));
        // Redistribute durations if frame is linked to dialogue
        const frame = get().timelineFrames.find((f) => f.id === id);
        if (frame?.dialogueId) {
          get().distributeFrameDurations(frame.dialogueId);
        }
      },
      approveTimeline: () => {
        get().updateProjectStatus('generating_video');
      },
      linkFrameToDialogue: (frameId, dialogueId) => {
        set((state) => ({
          timelineFrames: state.timelineFrames.map((f) =>
            f.id === frameId ? { ...f, dialogueId } : f
          ),
          dialogueBlocks: state.dialogueBlocks.map((d) =>
            d.id === dialogueId
              ? { ...d, linkedFrameIds: [...new Set([...d.linkedFrameIds, frameId])] }
              : d
          ),
        }));
        get().distributeFrameDurations(dialogueId);
      },
      unlinkFrameFromDialogue: (frameId) => {
        const frame = get().timelineFrames.find((f) => f.id === frameId);
        if (!frame?.dialogueId) return;
        const dialogueId = frame.dialogueId;
        
        set((state) => ({
          timelineFrames: state.timelineFrames.map((f) =>
            f.id === frameId ? { ...f, dialogueId: undefined } : f
          ),
          dialogueBlocks: state.dialogueBlocks.map((d) =>
            d.id === dialogueId
              ? { ...d, linkedFrameIds: d.linkedFrameIds.filter((id) => id !== frameId) }
              : d
          ),
        }));
        get().distributeFrameDurations(dialogueId);
      },
      distributeFrameDurations: (dialogueId) => {
        const dialogue = get().dialogueBlocks.find((d) => d.id === dialogueId);
        if (!dialogue || dialogue.linkedFrameIds.length === 0) return;
        
        const durationPerFrame = Math.max(2, dialogue.duration / dialogue.linkedFrameIds.length);
        
        set((state) => ({
          timelineFrames: state.timelineFrames.map((f) =>
            dialogue.linkedFrameIds.includes(f.id)
              ? { ...f, duration: Math.round(durationPerFrame * 10) / 10 }
              : f
          ),
        }));
      },

      // Workers
      workers: [],
      setWorkers: (workers) => set({ workers }),
      updateWorkerStatus: (id, status) =>
        set((state) => ({
          workers: state.workers.map((w) =>
            w.id === id ? { ...w, ...status } : w
          ),
        })),

      // Agent Chat
      messages: [],
      setMessages: (messages) => set({ messages }),
      addMessage: (message) =>
        set((state) => ({
          messages: [
            ...state.messages,
            {
              ...message,
              id: `msg-${Date.now()}`,
              timestamp: new Date(),
            },
          ],
        })),
      focusedItem: null,
      setFocusedItem: (item) => set({ focusedItem: item }),

      // Active Tab
      activeTab: 'outline',
      setActiveTab: (tab) => set({ activeTab: tab }),

      // Video
      videoExports: [],
      addVideoExport: (export_) =>
        set((state) => ({
          videoExports: [...state.videoExports, export_],
        })),
      workflow: { approvals: {}, changeRequests: [] },
      setWorkflow: (workflow) => set({ workflow }),
      reports: {},
      hydrateWorkspace: (snapshot) =>
        set((state) => ({
          currentProject: snapshot.project,
          projects: state.projects.some((project) => project.id === snapshot.project.id)
            ? state.projects.map((project) => (project.id === snapshot.project.id ? snapshot.project : project))
            : [snapshot.project, ...state.projects],
          creativeConcept: snapshot.creativeConcept,
          skeletonPlan: snapshot.skeletonPlan,
          scriptText: snapshot.scriptText,
          entities: snapshot.entities,
          storyboardFrames: snapshot.storyboardFrames,
          timelineFrames: snapshot.timelineFrames,
          dialogueBlocks: snapshot.dialogueBlocks,
          workers: snapshot.workers || [],
          messages: snapshot.messages || state.messages,
          workflow: snapshot.workflow || { approvals: {}, changeRequests: [] },
          reports: snapshot.reports || {},
        })),

      // UI State
      isChatOpen: true,
      setIsChatOpen: (open) => set({ isChatOpen: open }),
      isTimelineExpanded: false,
      setIsTimelineExpanded: (expanded) => set({ isTimelineExpanded: expanded }),
      isExportWizardOpen: false,
      setIsExportWizardOpen: (open) => set({ isExportWizardOpen: open }),
      mediaView: 'image',
      setMediaView: (view) => set({ mediaView: view }),
      
      // Mobile View State
      mobileView: 'chat',
      setMobileView: (view) => set({ mobileView: view }),
      isTimelineTrayOpen: false,
      setIsTimelineTrayOpen: (open) => set({ isTimelineTrayOpen: open }),
      highlightedItem: null,
      setHighlightedItem: (item) => set({ highlightedItem: item }),
      injectFocusToChat: (item) => {
        set({ highlightedItem: item, focusedItem: item });
        // Add a message draft with @ mention
        const draftMessage = `@${item.name} `;
        // Store draft in a way the chat input can access it
        window.__chatDraft = draftMessage;
      },
      
      // Multi-select State (Shift+Click)
      selectedItems: [],
      toggleItemSelection: (item, isShiftClick) => {
        set((state) => {
          const exists = state.selectedItems.find((i) => i.id === item.id);
          
          if (!isShiftClick) {
            // Without shift, replace selection with single item (or toggle off)
            if (exists && state.selectedItems.length === 1) {
              return { selectedItems: [] };
            }
            return { selectedItems: [item] };
          }
          
          // With shift, toggle in multi-select
          if (exists) {
            return { selectedItems: state.selectedItems.filter((i) => i.id !== item.id) };
          }
          return { selectedItems: [...state.selectedItems, item] };
        });
      },
      clearSelection: () => set({ selectedItems: [] }),
      isItemSelected: (id) => {
        return get().selectedItems.some((i) => i.id === id);
      },
    }),
    {
      name: 'morpheus-storage',
      version: 2,
      migrate: (persistedState: unknown) => {
        const state =
          persistedState && typeof persistedState === 'object'
            ? { ...(persistedState as PersistedMorpheusState) }
            : {};
        const isLegacyMockProjectId = (id: unknown) =>
          typeof id === 'string' && /^proj-\d+$/.test(id);

        const projects = Array.isArray(state.projects)
          ? state.projects.filter(
              (project): project is Project => Boolean(project) && !isLegacyMockProjectId(project.id)
            )
          : [];
        const currentProject =
          state.currentProject && !isLegacyMockProjectId(state.currentProject.id)
            ? state.currentProject
            : null;

        return {
          ...state,
          projects,
          currentProject,
        };
      },
      partialize: (state) => ({
        projects: state.projects,
        currentProject: state.currentProject,
        creativeConcept: state.creativeConcept,
        creativityLevel: state.creativityLevel,
        generationMode: state.generationMode,
        skeletonPlan: state.skeletonPlan,
        scriptText: state.scriptText,
        entities: state.entities,
        storyboardFrames: state.storyboardFrames,
        timelineFrames: state.timelineFrames,
        dialogueBlocks: state.dialogueBlocks,
        videoExports: state.videoExports,
        workflow: state.workflow,
        reports: state.reports,
      }),
    }
  )
);
