// Morpheus Studio Types

export type ProjectStatus = 
  | 'draft'
  | 'onboarding'
  | 'skeleton_review'
  | 'generating_assets'
  | 'reference_review'
  | 'generating_frames'
  | 'timeline_review'
  | 'generating_video'
  | 'complete';

export type CreativityLevel = 'strict' | 'balanced' | 'creative' | 'unbounded';

export type EntityType = 'cast' | 'location' | 'prop';

export type TabType = 'outline' | 'script' | 'cast' | 'locations' | 'props' | 'storyboard' | 'video';

export type GenerationMode = 'guided' | 'assisted' | 'open';

export type MediaView = 'prompt' | 'image' | 'video';

export type MediaStyle = 
  | 'new_digital_anime'
  | 'live_retro_grain'
  | 'chiaroscuro_live'
  | 'chiaroscuro_3d'
  | 'chiaroscuro_anime'
  | 'black_ink_anime'
  | 'live_soft_light'
  | 'live_clear';

export const MEDIA_STYLES: { id: MediaStyle; name: string; description: string; thumbnailUrl?: string }[] = [
  { id: 'new_digital_anime', name: 'New Digital Anime', description: 'High-fidelity polished anime rendering with clean linework and gradient shading.', thumbnailUrl: '/media-styles/new_digital_anime.jpg' },
  { id: 'live_retro_grain', name: 'Live Retro Grain', description: 'Warm live-action analog film emulation with fine grain and nostalgic studio softness.', thumbnailUrl: '/media-styles/live_retro_grain.jpg' },
  { id: 'chiaroscuro_live', name: 'Chiaroscuro Live', description: 'Moody live-action lighting with practical glow, deep shadows, and rich cinematic contrast.', thumbnailUrl: '/media-styles/chiaroscuro_live.jpg' },
  { id: 'chiaroscuro_3d', name: 'Chiaroscuro 3D', description: 'High-contrast 3D rendered worlds with dramatic light falloff and stylized night atmospheres.', thumbnailUrl: '/media-styles/chiaroscuro_3d.jpg' },
  { id: 'chiaroscuro_anime', name: 'Chiaroscuro Anime', description: 'Anime rendering pushed into dramatic warm-vs-cool contrast and shadow-heavy compositions.', thumbnailUrl: '/media-styles/chiaroscuro_anime.jpg' },
  { id: 'black_ink_anime', name: 'Black Ink Anime', description: 'Gritty cel-shaded animation with thick ink lines, stark blacks, and retro broadcast texture.', thumbnailUrl: '/media-styles/black_ink_anime.jpg' },
  { id: 'live_soft_light', name: 'Live Soft Light', description: 'Bright live-action softness with pastel warmth, shallow depth, and polished nostalgic film tone.', thumbnailUrl: '/media-styles/live_soft_light.jpg' },
  { id: 'live_clear', name: 'Live Clear', description: 'Sharp modern live-action clarity with hard directional light and clean digital precision.', thumbnailUrl: '/media-styles/live_clear.jpg' },
];

export interface Project {
  id: string;
  name: string;
  description: string;
  status: ProjectStatus;
  createdAt: Date;
  updatedAt: Date;
  creativityLevel: CreativityLevel;
  generationMode: GenerationMode;
  progress: number;
  coverImageUrl?: string | null;
  coverSummary?: string | null;
}

export interface CreativeConcept {
  title: string;
  logline: string;
  synopsis: string;
  tone: string;
  genre: string;
}

export interface SkeletonPlan {
  scenes: Scene[];
  totalScenes: number;
  estimatedDuration: number;
  markdown?: string;
}

export interface Scene {
  id: string;
  number: number;
  heading: string;
  description: string;
  location: string;
  characters: string[];
  estimatedFrames: number;
}

export interface Entity {
  id: string;
  type: EntityType;
  name: string;
  description: string;
  imageUrl?: string;
  status: 'pending' | 'generating' | 'complete' | 'error';
  metadata?: Record<string, unknown>;
}

export interface CastMember extends Entity {
  type: 'cast';
  age?: number;
  role?: string;
  appearance?: string;
}

export interface Location extends Entity {
  type: 'location';
  setting?: string;
  timeOfDay?: string;
}

export interface Prop extends Entity {
  type: 'prop';
  significance?: string;
}

export interface StoryboardFrame {
  id: string;
  sceneId: string;
  sequence: number;
  description: string;
  shotType: string;
  imageUrl?: string;
  status: 'pending' | 'generating' | 'complete' | 'approved';
}

export interface TimelineFrame {
  id: string;
  storyboardId: string;
  sequence: number;
  imageUrl?: string;
  prompt: string;
  status: 'pending' | 'generating' | 'complete' | 'approved';
  duration: number; // 1-15 seconds
  dialogueId?: string; // Linked dialogue block
}

export interface GraphCastFrameState {
  cast_id: string;
  frame_id: string;
  screen_position?: string | null;
  looking_at?: string | null;
  facing_direction?: string | null;
  spatial_position?: string | null;
  emotion?: string | null;
  action?: string | null;
}

export interface GraphPropFrameState {
  prop_id: string;
  frame_id: string;
  condition?: string | null;
  condition_detail?: string | null;
  holder_cast_id?: string | null;
  spatial_position?: string | null;
  visibility?: string | null;
  frame_role?: string | null;
}

export interface GraphLocationFrameState {
  location_id: string;
  frame_id: string;
  condition_modifiers?: string[];
  atmosphere_override?: string | null;
  lighting_override?: string | null;
  damage_level?: string | null;
}

export interface SceneGraphNode {
  scene_heading?: string | null;
  title?: string | null;
  emotional_arc?: string | null;
  location_id?: string | null;
  cast_present?: string[] | null;
  staging_plan?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface FrameGraphNode {
  scene_id?: string | null;
  narrative_beat?: string | null;
  source_text?: string | null;
  action_summary?: string | null;
  background?: ({ camera_facing?: string | null } & Record<string, unknown>) | null;
  composition?: ({
    shot?: string | null;
    angle?: string | null;
    blocking?: string | null;
  } & Record<string, unknown>) | null;
  directing?: ({
    movement_path?: string | null;
    reaction_target?: string | null;
  } & Record<string, unknown>) | null;
  [key: string]: unknown;
}

export interface DialogueGraphNode {
  [key: string]: unknown;
}

export interface FrameContext {
  frame: FrameGraphNode;
  scene: SceneGraphNode | null;
  dialogue: DialogueGraphNode[];
  castStates: GraphCastFrameState[];
  propStates: GraphPropFrameState[];
  locationStates: GraphLocationFrameState[];
}

export interface DialogueBlock {
  id: string;
  text: string;
  character: string;
  startFrame: number;
  endFrame: number;
  duration: number; // Required duration in seconds
  linkedFrameIds: string[]; // IDs of frames linked to this dialogue
}

export interface SelectedItem {
  type: 'cast' | 'location' | 'prop' | 'scene' | 'frame' | 'storyboard';
  id: string;
  name: string;
}

export interface TimelineSegment {
  id: string;
  frames: TimelineFrame[];
  dialogues: DialogueBlock[];
  startTime: number;
  endTime: number;
}

export interface AgentMessage {
  id: string;
  role: 'user' | 'agent';
  content: string;
  timestamp: Date;
  mode?: 'suggest' | 'apply' | 'regenerate';
  focusTarget?: {
    type: 'entity' | 'scene' | 'frame' | 'storyboard' | 'cast' | 'location' | 'prop';
    id: string;
    name: string;
  };
}

export interface WorkerStatus {
  id: string;
  name: string;
  status: 'idle' | 'running' | 'complete' | 'error';
  progress: number;
  message: string;
}

export interface CreativityLevelDefinition {
  level: CreativityLevel;
  name: string;
  description: string;
  freedoms: string[];
  constraints: string[];
}

export interface VideoExport {
  id: string;
  url: string;
  format: string;
  resolution: string;
  duration: number;
  createdAt: Date;
}

export interface WorkspaceReports {
  projectReport?: string | null;
  videoPromptProjection?: string | null;
  projectCover?: string | null;
  projectCoverSummary?: string | null;
  projectCoverMeta?: string | null;
  greenlightReport?: string | null;
  uiPhaseReport?: string | null;
}

export interface WorkflowState {
  approvals: Record<string, string>;
  changeRequests: Array<{
    gate: string;
    feedback: string;
    timestamp: string;
  }>;
}

export interface WorkspaceSnapshot {
  project: Project;
  creativeConcept: CreativeConcept;
  skeletonPlan: SkeletonPlan;
  scriptText: string;
  entities: Entity[];
  storyboardFrames: StoryboardFrame[];
  timelineFrames: TimelineFrame[];
  dialogueBlocks: DialogueBlock[];
  workers: WorkerStatus[];
  messages: AgentMessage[];
  workflow?: WorkflowState;
  reports?: WorkspaceReports;
}

export interface User {
  id: string;
  name: string;
  email: string;
  avatar?: string;
}
