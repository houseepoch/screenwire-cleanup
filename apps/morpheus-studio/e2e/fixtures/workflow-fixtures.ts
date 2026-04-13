const FIXTURE_TIMESTAMP = '2026-04-12T20:00:00.000Z';
const FIXTURE_PROJECT_ID = 'e2e-project';

function svgData(label: string, background: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540"><rect width="960" height="540" fill="${background}" /><text x="48" y="96" fill="#f8fafc" font-family="IBM Plex Sans, Arial, sans-serif" font-size="34" font-weight="700">${label}</text></svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

function createProject(status: string, progress: number) {
  return {
    id: FIXTURE_PROJECT_ID,
    name: 'E2E Clocktower',
    description: 'Deterministic workflow fixture for Morpheus Studio.',
    status,
    createdAt: FIXTURE_TIMESTAMP,
    updatedAt: FIXTURE_TIMESTAMP,
    creativityLevel: 'balanced',
    generationMode: 'assisted',
    progress,
    coverImageUrl: null,
    coverSummary: 'A moody clocktower sequence used to verify workflow gates.',
  };
}

function baseSnapshot(status: string, progress: number) {
  return {
    project: createProject(status, progress),
    creativeConcept: {
      title: 'Clocktower Drift',
      logline: 'A lone courier outruns a citywide blackout through the old clocktower district.',
      synopsis: 'A rain-soaked courier carries the final analog key through a city grid that is collapsing into darkness.',
      tone: 'Urgent, cinematic, rain-soaked',
      genre: 'Sci-fi thriller',
    },
    skeletonPlan: {
      scenes: [
        {
          id: 'scene-1',
          number: 1,
          heading: 'Clocktower approach',
          description: 'Courier sprints through neon rain toward the tower plaza.',
          location: 'Clocktower Plaza',
          characters: ['Mara'],
          estimatedFrames: 12,
        },
        {
          id: 'scene-2',
          number: 2,
          heading: 'Core handoff',
          description: 'Inside the tower, Mara slots the key while the city grid flickers.',
          location: 'Clocktower Core',
          characters: ['Mara', 'Archivist'],
          estimatedFrames: 14,
        },
      ],
      totalScenes: 2,
      estimatedDuration: 48,
      markdown: '## Clocktower Drift\n- Scene 1: plaza run\n- Scene 2: tower handoff',
    },
    scriptText: 'MARA: Keep the tower alive.\nARCHIVIST: Then don\'t miss.',
    entities: [
      {
        id: 'cast-mara',
        type: 'cast',
        name: 'Mara',
        description: 'Courier carrying the analog key.',
        status: 'complete',
        imageUrl: svgData('Mara', '#11243d'),
      },
      {
        id: 'location-clocktower',
        type: 'location',
        name: 'Clocktower Plaza',
        description: 'Rainy plaza surrounded by analog billboards.',
        status: 'complete',
        imageUrl: svgData('Clocktower Plaza', '#1f2937'),
      },
      {
        id: 'prop-key',
        type: 'prop',
        name: 'Analog Key',
        description: 'Mechanical key that reboots the tower core.',
        status: 'complete',
        imageUrl: svgData('Analog Key', '#3f2a56'),
      },
    ],
    storyboardFrames: [
      {
        id: 'board-1',
        sceneId: 'scene-1',
        sequence: 1,
        description: 'Wide shot of Mara cutting through rain and neon.',
        shotType: 'Wide',
        imageUrl: svgData('Storyboard 1', '#0f172a'),
        status: 'approved',
      },
      {
        id: 'board-2',
        sceneId: 'scene-2',
        sequence: 2,
        description: 'Close shot of the analog key reaching the clock core.',
        shotType: 'Close',
        imageUrl: svgData('Storyboard 2', '#0b1327'),
        status: 'approved',
      },
    ],
    timelineFrames: [
      {
        id: 'frame-1',
        storyboardId: 'board-1',
        sequence: 1,
        imageUrl: svgData('Frame 1', '#17324d'),
        prompt: 'Mara runs through rain-slick neon alley toward the clocktower.',
        status: 'approved',
        duration: 3,
        dialogueId: 'dialogue-1',
      },
      {
        id: 'frame-2',
        storyboardId: 'board-2',
        sequence: 2,
        imageUrl: svgData('Frame 2', '#33214a'),
        prompt: 'Clock core chamber glows as the analog key slides into place.',
        status: 'approved',
        duration: 4,
        dialogueId: 'dialogue-1',
      },
    ],
    dialogueBlocks: [
      {
        id: 'dialogue-1',
        text: 'Keep the tower alive.',
        character: 'MARA',
        startFrame: 1,
        endFrame: 2,
        duration: 7,
        linkedFrameIds: ['frame-1', 'frame-2'],
      },
    ],
    messages: [
      {
        id: 'msg-1',
        role: 'agent',
        content: 'Clocktower sequence is ready for review.',
        timestamp: FIXTURE_TIMESTAMP,
      },
    ],
    workers: [],
    workflow: {
      approvals: {},
      changeRequests: [],
    },
    reports: {
      projectReport: 'Clocktower fixture report',
      videoPromptProjection: 'Clocktower video prompt projection',
      greenlightReport: 'Greenlight ready',
      uiPhaseReport: 'UI fixture ready',
    },
  };
}

export const onboardingProject = createProject('onboarding', 0);

export const generatingAssetsSnapshot = {
  ...baseSnapshot('generating_assets', 35),
  workflow: {
    approvals: {},
    changeRequests: [],
  },
};

export const referenceReviewSnapshot = {
  ...baseSnapshot('reference_review', 58),
  workflow: {
    approvals: {},
    changeRequests: [],
  },
};

export const generatingFramesSnapshot = {
  ...baseSnapshot('generating_frames', 72),
  workflow: {
    approvals: {
      references: FIXTURE_TIMESTAMP,
    },
    changeRequests: [],
  },
};

export const timelineReviewSnapshot = {
  ...baseSnapshot('timeline_review', 82),
  workflow: {
    approvals: {
      references: FIXTURE_TIMESTAMP,
    },
    changeRequests: [],
  },
};

export const generatingVideoSnapshot = {
  ...baseSnapshot('generating_video', 90),
  workflow: {
    approvals: {
      references: FIXTURE_TIMESTAMP,
      timeline: FIXTURE_TIMESTAMP,
    },
    changeRequests: [],
  },
};

export const completeSnapshot = {
  ...baseSnapshot('complete', 100),
  workflow: {
    approvals: {
      references: FIXTURE_TIMESTAMP,
      timeline: FIXTURE_TIMESTAMP,
      video: FIXTURE_TIMESTAMP,
    },
    changeRequests: [],
  },
};

export const fixtureProjectId = FIXTURE_PROJECT_ID;
