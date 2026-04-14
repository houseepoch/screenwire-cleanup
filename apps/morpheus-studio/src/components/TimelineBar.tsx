import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { useMorpheusStore } from '../store';
import API, { preloadMediaWindow } from '../services/api';
import { useWindowSize } from '../hooks/useWindowSize';
import {
  Plus,
  Trash2,
  RefreshCw,
  Image,
  FileText,
  Play,
  MessageSquare,
  ChevronUp,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Pencil,
} from 'lucide-react';
import type { TimelineFrame, DialogueBlock } from '../types';

const PX_PER_SECOND = 50;
const MIN_DURATION = 2;
const MAX_DURATION = 15;
const MIN_FRAME_WIDTH = PX_PER_SECOND;
const MAX_FRAME_WIDTH = PX_PER_SECOND * MAX_DURATION;
const COMPACT_FRAME_HEIGHT = 92;
const EXPANDED_FRAME_HEIGHT = 156;
const TRIM_HANDLE_WIDTH = 12;
const TIMELINE_DIALOGUE_FONT_VAR = '--timeline-dialogue-font-size' as const;
const TIMELINE_PAGE_SIZE = 16;

type ResizeSide = 'left' | 'right';

interface FrameTimingDraft {
  duration: number;
  trimStart: number;
  trimEnd: number;
}

interface ResizeState {
  frameId: string;
  side: ResizeSide;
  lastX: number;
}

function roundTiming(value: number): number {
  return Math.round(value * 10) / 10;
}

function clampDuration(value: number): number {
  return roundTiming(Math.max(MIN_DURATION, Math.min(MAX_DURATION, value)));
}

function clampTrim(value: number): number {
  return roundTiming(Math.max(0, value));
}

function getDialogueFontSize(
  frameWidth: number,
  frameHeight: number,
  dialogueChars: number,
  dialogueLineCount: number,
): number {
  const usableWidth = Math.max(frameWidth - 20, 44);
  const usableHeight = Math.max(frameHeight - 24, 28);
  const estimatedLineCount = Math.max(1, dialogueLineCount);
  const maxFontSize = 34;
  const minFontSize = 9;
  const averageCharWidthRatio = 0.56;
  const lineHeightRatio = 1.16;
  const stackGap = 6;

  for (let fontSize = maxFontSize; fontSize >= minFontSize; fontSize -= 1) {
    const charsPerLine = Math.max(4, Math.floor(usableWidth / (fontSize * averageCharWidthRatio)));
    const wrappedLines = Math.max(estimatedLineCount, Math.ceil(dialogueChars / charsPerLine));
    const estimatedHeight =
      wrappedLines * fontSize * lineHeightRatio + Math.max(0, estimatedLineCount - 1) * stackGap;

    if (estimatedHeight <= usableHeight) {
      return fontSize;
    }
  }

  return minFontSize;
}

function getBaseTiming(frame: TimelineFrame): FrameTimingDraft {
  return {
    duration: clampDuration(frame.duration ?? 5),
    trimStart: clampTrim(frame.trimStart ?? 0),
    trimEnd: clampTrim(frame.trimEnd ?? 0),
  };
}

function applyResizeDelta(current: FrameTimingDraft, side: ResizeSide, deltaPx: number): FrameTimingDraft {
  if (!deltaPx) {
    return current;
  }

  let { duration, trimStart, trimEnd } = current;
  const deltaSecs = deltaPx / PX_PER_SECOND;

  if (side === 'left') {
    if (deltaSecs > 0) {
      const trimmed = Math.min(deltaSecs, Math.max(0, duration - MIN_DURATION));
      duration -= trimmed;
      trimStart += trimmed;
    } else if (deltaSecs < 0) {
      let extend = -deltaSecs;
      if (trimStart > 0) {
        const restored = Math.min(trimStart, extend);
        trimStart -= restored;
        duration += restored;
        extend -= restored;
      }
      if (extend > 0) {
        duration += extend;
      }
    }
  } else {
    if (deltaSecs < 0) {
      const trimmed = Math.min(-deltaSecs, Math.max(0, duration - MIN_DURATION));
      duration -= trimmed;
      trimEnd += trimmed;
    } else if (deltaSecs > 0) {
      let extend = deltaSecs;
      if (trimEnd > 0) {
        const restored = Math.min(trimEnd, extend);
        trimEnd -= restored;
        duration += restored;
        extend -= restored;
      }
      if (extend > 0) {
        duration += extend;
      }
    }
  }

  return {
    duration: clampDuration(duration),
    trimStart: clampTrim(trimStart),
    trimEnd: clampTrim(trimEnd),
  };
}

export function TimelineBar() {
  const {
    timelineFrames,
    dialogueBlocks,
    selectedFrameId,
    setSelectedFrameId,
    mediaView,
    setMediaView,
    regenerateFrame,
    removeFrame,
    expandFrame,
    updateFrameDuration,
    currentProject,
    hydrateWorkspace,
    isTimelineExpanded,
    setIsTimelineExpanded,
    toggleItemSelection,
    isItemSelected,
    injectFocusToChat,
  } = useMorpheusStore();
  const { height: windowHeight } = useWindowSize();

  const [showFrameMenu, setShowFrameMenu] = useState<string | null>(null);
  const [showDialogueOverlay, setShowDialogueOverlay] = useState(true);
  const [draftTimings, setDraftTimings] = useState<Record<string, FrameTimingDraft>>({});
  const [resizeState, setResizeState] = useState<ResizeState | null>(null);
  const [timelinePage, setTimelinePage] = useState(0);
  const [activeClipFrameId, setActiveClipFrameId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadTargetFrame, setUploadTargetFrame] = useState<string | null>(null);
  const draftTimingsRef = useRef<Record<string, FrameTimingDraft>>({});
  const resizeStateRef = useRef<ResizeState | null>(null);

  useEffect(() => {
    draftTimingsRef.current = draftTimings;
  }, [draftTimings]);

  useEffect(() => {
    resizeStateRef.current = resizeState;
  }, [resizeState]);

  const totalDuration = timelineFrames.reduce((acc, frame) => acc + frame.duration, 0);
  const timelineHeight = isTimelineExpanded ? 'min(50vh, 520px)' : '210px';
  const timelineMinHeight = isTimelineExpanded ? '300px' : '210px';
  const timelineViewportHeight = isTimelineExpanded ? Math.max(300, Math.min(windowHeight * 0.5, 520)) : 210;
  const timelinePageCount = Math.max(1, Math.ceil(timelineFrames.length / TIMELINE_PAGE_SIZE));
  const selectedTimelinePage = selectedFrameId
    ? timelineFrames.findIndex((frame) => frame.id === selectedFrameId)
    : -1;
  const currentTimelinePage = selectedTimelinePage >= 0
    ? Math.floor(selectedTimelinePage / TIMELINE_PAGE_SIZE)
    : Math.min(timelinePage, timelinePageCount - 1);
  const timelinePageStart = currentTimelinePage * TIMELINE_PAGE_SIZE;
  const visibleTimelineFrames = timelineFrames.slice(timelinePageStart, timelinePageStart + TIMELINE_PAGE_SIZE);
  const visibleStartSequence = visibleTimelineFrames[0]?.sequence ?? 0;
  const visibleEndSequence = visibleTimelineFrames[visibleTimelineFrames.length - 1]?.sequence ?? 0;
  const playableFrames = timelineFrames.filter((frame) => Boolean(frame.videoUrl));
  const activeClipFrame = activeClipFrameId
    ? timelineFrames.find((frame) => frame.id === activeClipFrameId) ?? null
    : null;
  const activePlayableIndex = activeClipFrame
    ? playableFrames.findIndex((frame) => frame.id === activeClipFrame.id)
    : -1;

  const getFrameTiming = (frame: TimelineFrame): FrameTimingDraft => draftTimings[frame.id] ?? getBaseTiming(frame);

  const getDialoguesForFrame = (frame: TimelineFrame): DialogueBlock[] => {
    return dialogueBlocks.filter((dialogue) => {
      const linkedToFrame = dialogue.linkedFrameIds?.includes(frame.id);
      const coversSequence =
        frame.sequence >= dialogue.startFrame &&
        frame.sequence <= dialogue.endFrame;
      const matchesPrimaryDialogue = frame.dialogueId && dialogue.id === frame.dialogueId;
      return linkedToFrame || coversSequence || Boolean(matchesPrimaryDialogue);
    });
  };

  const setTimelineSelection = useCallback((frameId: string) => {
    setSelectedFrameId(frameId);
    const frameIndex = timelineFrames.findIndex((item) => item.id === frameId);
    if (frameIndex >= 0) {
      setTimelinePage(Math.floor(frameIndex / TIMELINE_PAGE_SIZE));
    }
  }, [setSelectedFrameId, timelineFrames]);

  const openClipModal = useCallback((frameId: string) => {
    const frame = timelineFrames.find((item) => item.id === frameId);
    if (!frame?.videoUrl) {
      return;
    }
    setTimelineSelection(frameId);
    setActiveClipFrameId(frameId);
  }, [setTimelineSelection, timelineFrames]);

  const closeClipModal = useCallback(() => {
    setActiveClipFrameId(null);
  }, []);

  const stepPlayableClip = useCallback((direction: -1 | 1) => {
    if (activePlayableIndex < 0) {
      return;
    }
    const nextFrame = playableFrames[activePlayableIndex + direction];
    if (!nextFrame) {
      return;
    }
    setTimelineSelection(nextFrame.id);
    setActiveClipFrameId(nextFrame.id);
  }, [activePlayableIndex, playableFrames, setTimelineSelection]);

  const handleFrameClick = (frameId: string, event: React.MouseEvent) => {
    const isShiftClick = event.shiftKey;
    if (isShiftClick) {
      const frame = timelineFrames.find((item) => item.id === frameId);
      if (frame) {
        const focused = { type: 'frame' as const, id: frameId, name: `Frame ${frame.sequence}` };
        toggleItemSelection(focused, true);
        injectFocusToChat(focused);
      }
    } else {
      setTimelineSelection(frameId);
      if (mediaView === 'video') {
        const frame = timelineFrames.find((item) => item.id === frameId);
        if (frame?.videoUrl) {
          setActiveClipFrameId(frameId);
        }
      }
    }
  };

  useEffect(() => {
    const preloadFrames = timelineFrames.slice(
      Math.max(0, timelinePageStart - TIMELINE_PAGE_SIZE),
      Math.min(timelineFrames.length, timelinePageStart + TIMELINE_PAGE_SIZE * 2),
    );
    preloadMediaWindow(
      preloadFrames.map((frame) => ({
        imageUrl: frame.thumbnailUrl || frame.imageUrl,
        videoUrl: frame.videoUrl,
        posterUrl: frame.thumbnailUrl || frame.imageUrl,
      })),
    );
  }, [timelineFrames, timelinePageStart]);

  useEffect(() => {
    if (!activeClipFrame) {
      return;
    }
    const nearby = playableFrames.slice(
      Math.max(0, activePlayableIndex - 1),
      Math.min(playableFrames.length, activePlayableIndex + 2),
    );
    preloadMediaWindow(
      nearby.map((frame) => ({
        imageUrl: frame.thumbnailUrl || frame.imageUrl,
        videoUrl: frame.videoUrl,
        posterUrl: frame.thumbnailUrl || frame.imageUrl,
      })),
    );
  }, [activeClipFrame, activePlayableIndex, playableFrames]);

  useEffect(() => {
    if (mediaView !== 'video') {
      const timeoutId = window.setTimeout(closeClipModal, 0);
      return () => window.clearTimeout(timeoutId);
    }
    return undefined;
  }, [closeClipModal, mediaView]);

  useEffect(() => {
    if (!activeClipFrameId) {
      return;
    }
    if (!timelineFrames.some((frame) => frame.id === activeClipFrameId && frame.videoUrl)) {
      const timeoutId = window.setTimeout(closeClipModal, 0);
      return () => window.clearTimeout(timeoutId);
    }
    return undefined;
  }, [activeClipFrameId, closeClipModal, timelineFrames]);

  useEffect(() => {
    if (!activeClipFrameId) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setActiveClipFrameId(null);
        return;
      }
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        stepPlayableClip(-1);
        return;
      }
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        stepPlayableClip(1);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeClipFrameId, stepPlayableClip]);

  const persistFrameTiming = useCallback(async (frameId: string, timing: FrameTimingDraft) => {
    updateFrameDuration(frameId, timing.duration);
    if (!currentProject) {
      return;
    }
    try {
      await API.timeline.updateFrame(currentProject.id, frameId, {
        duration: timing.duration,
        trimStart: timing.trimStart,
        trimEnd: timing.trimEnd,
      });
      const snapshot = await API.workspace.get(currentProject.id);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to persist frame timing:', error);
    }
  }, [currentProject, hydrateWorkspace, updateFrameDuration]);

  useEffect(() => {
    if (!resizeState) {
      return;
    }

    const handlePointerMove = (event: PointerEvent) => {
      const active = resizeStateRef.current;
      if (!active) {
        return;
      }
      const deltaPx = event.clientX - active.lastX;
      if (!deltaPx) {
        return;
      }
      const frame = timelineFrames.find((item) => item.id === active.frameId);
      if (!frame) {
        return;
      }
      const currentTiming = draftTimingsRef.current[active.frameId] ?? getBaseTiming(frame);
      const nextTiming = applyResizeDelta(currentTiming, active.side, deltaPx);
      const nextDrafts = {
        ...draftTimingsRef.current,
        [active.frameId]: nextTiming,
      };
      draftTimingsRef.current = nextDrafts;
      setDraftTimings(nextDrafts);
      setResizeState((current) => (current ? { ...current, lastX: event.clientX } : current));
    };

    const handlePointerUp = () => {
      const active = resizeStateRef.current;
      if (!active) {
        return;
      }
      const frame = timelineFrames.find((item) => item.id === active.frameId);
      const timing = frame ? (draftTimingsRef.current[active.frameId] ?? getBaseTiming(frame)) : null;
      if (timing) {
        void persistFrameTiming(active.frameId, timing);
      }
      const nextDrafts = { ...draftTimingsRef.current };
      delete nextDrafts[active.frameId];
      draftTimingsRef.current = nextDrafts;
      setDraftTimings(nextDrafts);
      setResizeState(null);
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);

    return () => {
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };
  }, [resizeState, timelineFrames, persistFrameTiming]);

  const handleResizeStart = (event: React.PointerEvent, frameId: string, side: ResizeSide) => {
    event.preventDefault();
    event.stopPropagation();
    setShowFrameMenu(null);
    setResizeState({
      frameId,
      side,
      lastX: event.clientX,
    });
  };

  const handleFileUpload = async (frameId: string, file: File) => {
    if (!currentProject) {
      return;
    }
    try {
      await API.timeline.uploadFrame(currentProject.id, frameId, file);
      const snapshot = await API.workspace.get(currentProject.id);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to upload frame image:', error);
    }
  };

  const handleRegenerateFrame = async (frameId: string) => {
    if (!currentProject) {
      regenerateFrame(frameId);
      return;
    }
    try {
      await API.timeline.regenerateFrame(currentProject.id, frameId);
      const snapshot = await API.workspace.get(currentProject.id);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to regenerate frame:', error);
    }
  };

  const handleEditFrame = async (frameId: string) => {
    if (!currentProject) {
      return;
    }
    const prompt = window.prompt('Describe the image edit you want Morpheus to apply to this frame.');
    if (!prompt?.trim()) {
      return;
    }
    try {
      await API.timeline.editFrame(currentProject.id, frameId, prompt.trim());
      const snapshot = await API.workspace.get(currentProject.id);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to edit frame:', error);
    }
  };

  const handleRemoveFrame = async (frameId: string) => {
    if (!currentProject) {
      removeFrame(frameId);
      return;
    }
    try {
      await API.timeline.removeFrame(currentProject.id, frameId);
      const snapshot = await API.workspace.get(currentProject.id);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to remove frame:', error);
    }
  };

  const handleExpandFrame = async (frameId: string, direction: 'before' | 'after') => {
    if (!currentProject) {
      expandFrame(frameId, direction);
      return;
    }
    try {
      await API.timeline.expandFrame(currentProject.id, frameId, direction);
      const snapshot = await API.workspace.get(currentProject.id);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to expand frame:', error);
    }
  };

  const renderMediaContent = (frame: TimelineFrame, size: 'small' | 'large' = 'small') => {
    const isLarge = size === 'large';
    const posterUrl = frame.thumbnailUrl || frame.imageUrl;

    switch (mediaView) {
      case 'prompt':
        return (
          <div
            style={{
              padding: isLarge ? '8px 12px' : '4px 6px',
              fontSize: isLarge ? '10px' : '8px',
              color: 'var(--text-secondary)',
              overflow: 'hidden',
              lineHeight: 1.3,
              height: '100%',
              display: 'flex',
              alignItems: 'center',
            }}
          >
            <span
              style={{
                display: '-webkit-box',
                WebkitLineClamp: isLarge ? 5 : 3,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}
            >
              {frame.prompt}
            </span>
          </div>
        );
      case 'video':
        return frame.videoUrl ? (
          <div className="timeline-video-card">
            <video
              key={`${frame.id}-${frame.videoUrl}`}
              className="timeline-video-preview"
              src={frame.videoUrl}
              poster={posterUrl}
              preload="metadata"
              muted
              playsInline
            />
            <div className="timeline-video-sheen" />
            <button
              type="button"
              className="timeline-video-launch"
              onClick={(event) => {
                event.stopPropagation();
                openClipModal(frame.id);
              }}
              aria-label={`Play clip for frame ${frame.sequence}`}
            >
              <span className="timeline-video-launch-icon">
                <Play size={isLarge ? 13 : 10} fill="currentColor" />
              </span>
            </button>
          </div>
        ) : (
          <div className="timeline-video-missing">
            <span className="timeline-video-missing-icon">
              <Play size={isLarge ? 18 : 14} />
            </span>
            <span className="timeline-video-missing-label">Clip Pending</span>
          </div>
        );
      case 'image':
      default:
        return (frame.thumbnailUrl || frame.imageUrl) ? (
          <img
            src={frame.thumbnailUrl || frame.imageUrl}
            alt={`Frame ${frame.sequence}`}
            loading="lazy"
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          />
        ) : (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              background: 'var(--bg-tertiary)',
              gap: '4px',
              cursor: 'pointer',
            }}
            onClick={(event) => {
              event.stopPropagation();
              setUploadTargetFrame(frame.id);
              fileInputRef.current?.click();
            }}
          >
            <Image size={isLarge ? 24 : 16} style={{ color: 'var(--text-muted)' }} />
            <span style={{ fontSize: isLarge ? '10px' : '8px', color: 'var(--text-muted)' }}>Click to upload</span>
          </div>
        );
    }
  };

  const renderFrame = (frame: TimelineFrame) => {
    const isSelected = selectedFrameId === frame.id;
    const isMultiSelected = isItemSelected(frame.id);
    const frameDialogues = getDialoguesForFrame(frame);
    const hasDialogue = frameDialogues.length > 0;
    const timing = getFrameTiming(frame);
    const isResizing = resizeState?.frameId === frame.id;
    const resizeSide = isResizing ? resizeState?.side : null;
    const frameWidth = Math.max(
      MIN_FRAME_WIDTH,
      Math.min(MAX_FRAME_WIDTH, Math.round(timing.duration * PX_PER_SECOND)),
    );
    const frameHeight = isTimelineExpanded ? EXPANDED_FRAME_HEIGHT : COMPACT_FRAME_HEIGHT;
    const dialogueChars = frameDialogues.reduce(
      (total, dialogue) => total + `${dialogue.character}: ${dialogue.text}`.length,
      0,
    );
    const dialogueOverlayStyle: CSSProperties & Record<typeof TIMELINE_DIALOGUE_FONT_VAR, string> = {
      [TIMELINE_DIALOGUE_FONT_VAR]: `${getDialogueFontSize(frameWidth, frameHeight, dialogueChars, frameDialogues.length)}px`,
    };
    const shellStyle: CSSProperties = {
      width: `${frameWidth + TRIM_HANDLE_WIDTH * 2}px`,
      gridTemplateColumns: `${TRIM_HANDLE_WIDTH}px minmax(0, ${frameWidth}px) ${TRIM_HANDLE_WIDTH}px`,
    };
    const trimStartWidth = Math.min(frameWidth * 0.18, Math.max(0, timing.trimStart * 14));
    const trimEndWidth = Math.min(frameWidth * 0.18, Math.max(0, timing.trimEnd * 14));

    return (
      <div
        key={frame.id}
        className={`timeline-frame ${isTimelineExpanded ? 'expanded' : 'compact'}`}
      >
        <div className="timeline-frame-shell" style={shellStyle}>
          <button
            type="button"
            className={`timeline-trim-handle timeline-trim-handle-left ${resizeSide === 'left' ? 'is-active' : ''}`}
            onPointerDown={(event) => handleResizeStart(event, frame.id, 'left')}
            aria-label={`Trim or extend frame ${frame.sequence} from the left`}
          >
            <ChevronLeft size={14} />
          </button>
          <div
            className={`timeline-frame-thumb ${isSelected ? 'selected' : ''} ${isTimelineExpanded ? 'expanded' : 'compact'} ${
              isResizing ? 'is-resizing' : ''
            }`}
            onClick={(event) => handleFrameClick(frame.id, event)}
            onDoubleClick={() => setShowFrameMenu(showFrameMenu === frame.id ? null : frame.id)}
            style={{
              position: 'relative',
              width: '100%',
              border: isMultiSelected
                ? '2px solid var(--success)'
                : hasDialogue
                  ? '2px solid rgba(101, 211, 255, 0.88)'
                  : undefined,
              boxShadow: isMultiSelected
                ? '0 0 8px var(--success)'
                : hasDialogue
                  ? '0 0 16px rgba(101, 211, 255, 0.18)'
                  : undefined,
            }}
          >
            {renderMediaContent(frame, isTimelineExpanded ? 'large' : 'small')}

            {timing.trimStart > 0 ? (
              <div className="timeline-trim-overlay timeline-trim-overlay-left" style={{ width: `${trimStartWidth}px` }} />
            ) : null}
            {timing.trimEnd > 0 ? (
              <div className="timeline-trim-overlay timeline-trim-overlay-right" style={{ width: `${trimEndWidth}px` }} />
            ) : null}

            {showDialogueOverlay && hasDialogue ? (
              <div
                className="timeline-dialogue-overlay"
                data-testid={`timeline-dialogue-overlay-${frame.id}`}
                style={dialogueOverlayStyle}
              >
                <div className="timeline-dialogue-stack">
                  {frameDialogues.map((dialogue) => (
                    <div key={`${frame.id}-${dialogue.id}`} className="timeline-dialogue-line">
                      <span className="timeline-dialogue-speaker">{dialogue.character}:</span>{' '}
                      <span className="timeline-dialogue-copy">{dialogue.text}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            <div className={`timeline-duration-badge ${isResizing ? 'is-active' : ''}`}>
              {timing.duration.toFixed(1)}s
            </div>

            {showFrameMenu === frame.id ? (
              <div
                style={{
                  position: 'absolute',
                  bottom: '100%',
                  left: '50%',
                  transform: 'translateX(-50%)',
                  marginBottom: '8px',
                  background: 'var(--bg-secondary)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: '6px',
                  padding: '4px',
                  zIndex: 100,
                  minWidth: '90px',
                  boxShadow: '0 4px 12px rgba(0, 0, 0, 0.3)',
                }}
              >
                <button
                  onClick={(event) => {
                    event.stopPropagation();
                    void handleRegenerateFrame(frame.id);
                    setShowFrameMenu(null);
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '6px 8px',
                    width: '100%',
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-primary)',
                    fontSize: '10px',
                    cursor: 'pointer',
                    borderRadius: '3px',
                    textAlign: 'left',
                  }}
                >
                  <RefreshCw size={10} />
                  Regen
                </button>
                <button
                  onClick={(event) => {
                    event.stopPropagation();
                    void handleEditFrame(frame.id);
                    setShowFrameMenu(null);
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '6px 8px',
                    width: '100%',
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-primary)',
                    fontSize: '10px',
                    cursor: 'pointer',
                    borderRadius: '3px',
                    textAlign: 'left',
                  }}
                >
                  <Pencil size={10} />
                  Edit
                </button>
                <button
                  onClick={(event) => {
                    event.stopPropagation();
                    void handleExpandFrame(frame.id, 'before');
                    setShowFrameMenu(null);
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '6px 8px',
                    width: '100%',
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-primary)',
                    fontSize: '10px',
                    cursor: 'pointer',
                    borderRadius: '3px',
                    textAlign: 'left',
                  }}
                >
                  <Plus size={10} />
                  Add
                </button>
                <button
                  onClick={(event) => {
                    event.stopPropagation();
                    void handleRemoveFrame(frame.id);
                    setShowFrameMenu(null);
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    padding: '6px 8px',
                    width: '100%',
                    background: 'none',
                    border: 'none',
                    color: 'var(--error)',
                    fontSize: '10px',
                    cursor: 'pointer',
                    borderRadius: '3px',
                    textAlign: 'left',
                  }}
                >
                  <Trash2 size={10} />
                  Del
                </button>
              </div>
            ) : null}
          </div>
          <button
            type="button"
            className={`timeline-trim-handle timeline-trim-handle-right ${resizeSide === 'right' ? 'is-active' : ''}`}
            onPointerDown={(event) => handleResizeStart(event, frame.id, 'right')}
            aria-label={`Trim or extend frame ${frame.sequence} from the right`}
          >
            <ChevronRight size={14} />
          </button>
        </div>

        <span className="timeline-frame-number">#{frame.sequence}</span>
      </div>
    );
  };

  const clipModal = activeClipFrame?.videoUrl
    ? createPortal(
        <>
          <button
            type="button"
            className="timeline-clip-backdrop"
            style={{ bottom: `${timelineViewportHeight}px` }}
            onClick={() => setActiveClipFrameId(null)}
            aria-label="Close clip preview"
          />
          <div
            className="timeline-clip-modal-shell"
            style={{ bottom: `${timelineViewportHeight + 18}px` }}
          >
            <div
              className="timeline-clip-modal"
              role="dialog"
              aria-modal="false"
              aria-label={`Clip preview for frame ${activeClipFrame.sequence}`}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="timeline-clip-modal-header">
                <div className="timeline-clip-modal-copy">
                  <span className="timeline-clip-modal-kicker">Timeline Clip</span>
                  <h3>Frame {activeClipFrame.sequence}</h3>
                  <p>
                    Use left/right arrow keys or click another clip in the timeline to keep browsing.
                  </p>
                </div>
                <div className="timeline-clip-modal-actions">
                  <button
                    type="button"
                    className="timeline-clip-nav-btn"
                    onClick={() => stepPlayableClip(-1)}
                    disabled={activePlayableIndex <= 0}
                    aria-label="Play previous clip"
                  >
                    <ChevronLeft size={16} />
                  </button>
                  <button
                    type="button"
                    className="timeline-clip-nav-btn"
                    onClick={() => stepPlayableClip(1)}
                    disabled={activePlayableIndex < 0 || activePlayableIndex >= playableFrames.length - 1}
                    aria-label="Play next clip"
                  >
                    <ChevronRight size={16} />
                  </button>
                  <button
                    type="button"
                    className="timeline-clip-close-btn"
                    onClick={() => setActiveClipFrameId(null)}
                  >
                    Close
                  </button>
                </div>
              </div>
              <div className="timeline-clip-modal-stage">
                <video
                  key={activeClipFrame.id}
                  className="timeline-clip-player"
                  src={activeClipFrame.videoUrl}
                  poster={activeClipFrame.imageUrl}
                  controls
                  autoPlay
                  playsInline
                  preload="auto"
                />
              </div>
            </div>
          </div>
        </>,
        document.body,
      )
    : null;

  return (
    <div
      className={`timeline-bar ${activeClipFrame ? 'clip-modal-open' : ''}`.trim()}
      data-testid="timeline-bar"
      style={{
        height: timelineHeight,
        minHeight: timelineMinHeight,
        transition: 'height 0.3s ease',
      }}
    >
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file && uploadTargetFrame) {
            void handleFileUpload(uploadTargetFrame, file);
          }
          setUploadTargetFrame(null);
        }}
      />

      <div className="timeline-bar-header">
        <div className="timeline-bar-side timeline-bar-side-left">
          <button
            type="button"
            className="timeline-header-btn"
            onClick={() => setIsTimelineExpanded(!isTimelineExpanded)}
          >
            {isTimelineExpanded ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
          </button>
          <span className="timeline-bar-title">Timeline</span>
          <span className="timeline-bar-meta">
            {timelineFrames.length} frames · {totalDuration.toFixed(1)}s
          </span>
        </div>

        <div className="timeline-media-toggle">
          {(['image', 'prompt', 'video'] as const).map((view) => (
            <button
              key={view}
              type="button"
              data-testid={`timeline-media-toggle-${view}`}
              onClick={() => setMediaView(view)}
              className={`timeline-media-pill ${mediaView === view ? 'active' : ''}`}
            >
              {view === 'image' && <Image size={12} />}
              {view === 'prompt' && <FileText size={12} />}
              {view === 'video' && <Play size={12} />}
              {view}
            </button>
          ))}
          <button
            type="button"
            data-testid="timeline-dialogue-toggle"
            onClick={() => setShowDialogueOverlay((current) => !current)}
            className={`timeline-media-pill ${showDialogueOverlay ? 'active' : ''}`}
          >
            <MessageSquare size={12} />
            Display Dialogue
          </button>
        </div>

        <div className="timeline-bar-side timeline-bar-side-right">
          {timelinePageCount > 1 ? (
            <div className="collection-pagination collection-pagination-compact timeline-pagination">
              <button
                type="button"
                className="collection-pagination-btn"
                onClick={() => {
                  setSelectedFrameId(null);
                  setTimelinePage(Math.max(0, currentTimelinePage - 1));
                }}
                disabled={currentTimelinePage === 0}
                aria-label="Show previous timeline page"
              >
                <ChevronLeft size={14} />
              </button>
              <div className="collection-pagination-copy">
                <span className="collection-pagination-kicker">Timeline window</span>
                <span className="collection-pagination-label">
                  {visibleStartSequence}-{visibleEndSequence} of {timelineFrames.length}
                </span>
              </div>
              <button
                type="button"
                className="collection-pagination-btn"
                onClick={() => {
                  setSelectedFrameId(null);
                  setTimelinePage(Math.min(timelinePageCount - 1, currentTimelinePage + 1));
                }}
                disabled={currentTimelinePage >= timelinePageCount - 1}
                aria-label="Show next timeline page"
              >
                <ChevronRight size={14} />
              </button>
            </div>
          ) : null}
        </div>
      </div>

      <div className="timeline-bar-content">
        <div className="timeline-rail timeline-linear-strip" data-testid="timeline-linear-strip">
          {visibleTimelineFrames.map((frame) => renderFrame(frame))}
        </div>
      </div>
      {clipModal}
    </div>
  );
}
