import { useEffect, useRef, useState, type CSSProperties, type Dispatch, type SetStateAction } from 'react';
import { useMorpheusStore } from '../store';
import API, { preloadMediaWindow } from '../services/api';
import { 
  FileText, 
  Scroll, 
  Users, 
  MapPin, 
  Package, 
  LayoutGrid, 
  Play,
  Video,
  ArrowRight,
  Download,
  Check,
  Clock,
  AlertCircle,
  Upload,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import type {
  TabType,
  Entity,
  SelectedItem,
  FrameContext,
  GraphCastFrameState,
  GraphPropFrameState,
  GraphLocationFrameState,
} from '../types';

const tabs: { id: TabType; label: string; icon: React.ElementType }[] = [
  { id: 'outline', label: 'Outline', icon: FileText },
  { id: 'script', label: 'Script', icon: Scroll },
  { id: 'cast', label: 'Cast', icon: Users },
  { id: 'locations', label: 'Locations', icon: MapPin },
  { id: 'props', label: 'Props', icon: Package },
  { id: 'storyboard', label: 'Storyboard', icon: LayoutGrid },
  { id: 'video', label: 'Video', icon: Play },
];

interface DragState {
  isDragging: boolean;
  entityId: string | null;
}

interface SceneStagingPhase {
  cast_positions?: unknown;
  cast_looking_at?: unknown;
  cast_facing?: unknown;
}

interface SceneStagingPlan {
  start?: SceneStagingPhase;
  mid?: SceneStagingPhase;
  end?: SceneStagingPhase;
}

function formatJsonMap(value: unknown): string {
  try {
    return JSON.stringify(value || {}, null, 2);
  } catch {
    return '{}';
  }
}

function parseJsonMap(value: string): Record<string, string> {
  if (!value.trim()) {
    return {};
  }
  const parsed = JSON.parse(value);
  return typeof parsed === 'object' && parsed !== null ? parsed as Record<string, string> : {};
}

const STORYBOARD_PAGE_SIZE = 9;
const ENTITY_PAGE_SIZES = {
  cast: 6,
  locations: 6,
  props: 8,
} as const;

type EntityTabKey = keyof typeof ENTITY_PAGE_SIZES;

export function DetailPanel() {
  const { 
    activeTab, 
    setActiveTab, 
    entities, 
    storyboardFrames,
    timelineFrames,
    selectedFrameId,
    setSelectedFrameId,
    setIsExportWizardOpen,
    skeletonPlan,
    scriptText,
    currentProject,
    hydrateWorkspace,
    toggleItemSelection,
    isItemSelected,
    selectedItems,
    uploadEntityImage,
    injectFocusToChat,
    clearSelection,
    reports,
  } = useMorpheusStore();

  const [dragState, setDragState] = useState<DragState>({ isDragging: false, entityId: null });
  const fileInputRef = useRef<HTMLInputElement>(null);
  const videoPreviewRef = useRef<HTMLVideoElement>(null);
  const [uploadTargetEntity, setUploadTargetEntity] = useState<string | null>(null);
  const [editorTitle, setEditorTitle] = useState('');
  const [editorDescription, setEditorDescription] = useState('');
  const [editorLocation, setEditorLocation] = useState('');
  const [editorCharacters, setEditorCharacters] = useState('');
  const [isSavingSelection, setIsSavingSelection] = useState(false);
  const [frameContext, setFrameContext] = useState<FrameContext | null>(null);
  const [frameNarrativeBeat, setFrameNarrativeBeat] = useState('');
  const [frameActionSummary, setFrameActionSummary] = useState('');
  const [frameCameraFacing, setFrameCameraFacing] = useState('');
  const [frameShot, setFrameShot] = useState('');
  const [frameAngle, setFrameAngle] = useState('');
  const [frameBlocking, setFrameBlocking] = useState('');
  const [frameMovementPath, setFrameMovementPath] = useState('');
  const [frameReactionTarget, setFrameReactionTarget] = useState('');
  const [frameCastStates, setFrameCastStates] = useState<GraphCastFrameState[]>([]);
  const [framePropStates, setFramePropStates] = useState<GraphPropFrameState[]>([]);
  const [frameLocationStates, setFrameLocationStates] = useState<GraphLocationFrameState[]>([]);
  const [isVideoPreviewPlaying, setIsVideoPreviewPlaying] = useState(false);
  const [videoPlaybackMode, setVideoPlaybackMode] = useState<'isolate' | 'playthrough'>('isolate');
  const [shouldAutoplayQueuedVideo, setShouldAutoplayQueuedVideo] = useState(false);
  const [sceneStartPositions, setSceneStartPositions] = useState('{}');
  const [sceneStartLooking, setSceneStartLooking] = useState('{}');
  const [sceneStartFacing, setSceneStartFacing] = useState('{}');
  const [sceneMidPositions, setSceneMidPositions] = useState('{}');
  const [sceneMidLooking, setSceneMidLooking] = useState('{}');
  const [sceneMidFacing, setSceneMidFacing] = useState('{}');
  const [sceneEndPositions, setSceneEndPositions] = useState('{}');
  const [sceneEndLooking, setSceneEndLooking] = useState('{}');
  const [sceneEndFacing, setSceneEndFacing] = useState('{}');
  const [storyboardPage, setStoryboardPage] = useState(0);
  const [entityPages, setEntityPages] = useState<Record<EntityTabKey, number>>({
    cast: 0,
    locations: 0,
    props: 0,
  });

  const cast = entities.filter((e): e is Entity & { type: 'cast' } => e.type === 'cast');
  const locations = entities.filter((e): e is Entity & { type: 'location' } => e.type === 'location');
  const props = entities.filter((e): e is Entity & { type: 'prop' } => e.type === 'prop');
  const selectedItem = selectedItems.length === 1 ? selectedItems[0] : null;
  const selectedScene = selectedItem?.type === 'scene'
    ? skeletonPlan?.scenes.find((scene) => scene.id === selectedItem.id) ?? null
    : null;
  const selectedFrame = selectedItem && ['frame', 'storyboard'].includes(selectedItem.type)
    ? selectedItem
    : null;
  const selectedEntity = selectedItem && ['cast', 'location', 'prop'].includes(selectedItem.type)
    ? entities.find((entity) => entity.id === selectedItem.id) ?? null
    : null;
  const storyboardPageCount = Math.max(1, Math.ceil(storyboardFrames.length / STORYBOARD_PAGE_SIZE));
  const selectedStoryboardPage = selectedFrame
    ? storyboardFrames.findIndex((frame) => frame.id === selectedFrame.id)
    : -1;
  const currentStoryboardPage = selectedStoryboardPage >= 0
    ? Math.floor(selectedStoryboardPage / STORYBOARD_PAGE_SIZE)
    : Math.min(storyboardPage, storyboardPageCount - 1);
  const storyboardPageStart = currentStoryboardPage * STORYBOARD_PAGE_SIZE;
  const storyboardPageFrames = storyboardFrames.slice(storyboardPageStart, storyboardPageStart + STORYBOARD_PAGE_SIZE);

  const getEntityPageData = (tabKey: EntityTabKey, items: Entity[]) => {
    const pageSize = ENTITY_PAGE_SIZES[tabKey];
    const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
    const selectedIndex =
      selectedEntity && (
        (tabKey === 'cast' && selectedEntity.type === 'cast') ||
        (tabKey === 'locations' && selectedEntity.type === 'location') ||
        (tabKey === 'props' && selectedEntity.type === 'prop')
      )
        ? items.findIndex((item) => item.id === selectedEntity.id)
        : -1;
    const currentPage = selectedIndex >= 0
      ? Math.floor(selectedIndex / pageSize)
      : Math.min(entityPages[tabKey], pageCount - 1);
    const pageStart = currentPage * pageSize;

    return {
      pageCount,
      currentPage,
      pageStart,
      pageEnd: Math.min(items.length, pageStart + pageSize),
      items: items.slice(pageStart, pageStart + pageSize),
    };
  };

  const castPage = getEntityPageData('cast', cast);
  const locationPage = getEntityPageData('locations', locations);
  const propPage = getEntityPageData('props', props);
  const selectedTimelineFrame = selectedFrameId
    ? timelineFrames.find((frame) => frame.id === selectedFrameId) ?? null
    : null;
  const previewVideoFrame =
    (selectedTimelineFrame?.videoUrl ? selectedTimelineFrame : null) ??
    timelineFrames.find((frame) => frame.videoUrl) ??
    null;
  const previewVideoUrl = reports.finalExport || previewVideoFrame?.videoUrl || null;
  const previewPosterUrl = previewVideoFrame?.thumbnailUrl || previewVideoFrame?.imageUrl;
  const previewVideoLabel = reports.finalExport ? 'Final Export' : previewVideoFrame ? 'Timeline Clip' : 'Video Preview';
  const previewVideoTitle = reports.finalExport
    ? currentProject?.name || 'Final export'
    : previewVideoFrame
      ? `Frame ${previewVideoFrame.sequence}`
      : 'Render output pending';
  const playableTimelineFrames = timelineFrames.filter((frame) => Boolean(frame.videoUrl));
  const selectedFrameIds = new Set(
    selectedItems
      .filter((item) => item.type === 'frame' || item.type === 'storyboard')
      .map((item) => item.id),
  );
  const isolatedPlayableFrames = playableTimelineFrames.filter((frame) => selectedFrameIds.has(frame.id));
  const playbackQueue = videoPlaybackMode === 'playthrough'
    ? playableTimelineFrames
    : isolatedPlayableFrames.length > 0
      ? isolatedPlayableFrames
      : previewVideoFrame?.videoUrl
        ? [previewVideoFrame]
        : [];
  const currentPlaybackQueueIndex = previewVideoFrame
    ? playbackQueue.findIndex((frame) => frame.id === previewVideoFrame.id)
    : -1;

  useEffect(() => {
    preloadMediaWindow([
      ...cast.slice(0, ENTITY_PAGE_SIZES.cast).map((entity) => ({
        imageUrl: entity.thumbnailUrl || entity.imageUrl,
      })),
      ...locations.slice(0, ENTITY_PAGE_SIZES.locations).map((entity) => ({
        imageUrl: entity.thumbnailUrl || entity.imageUrl,
      })),
      ...props.slice(0, ENTITY_PAGE_SIZES.props).map((entity) => ({
        imageUrl: entity.thumbnailUrl || entity.imageUrl,
      })),
      ...storyboardFrames.slice(0, STORYBOARD_PAGE_SIZE).map((frame) => ({
        imageUrl: frame.thumbnailUrl || frame.imageUrl,
      })),
    ]);
  }, [cast, locations, props, storyboardFrames]);

  useEffect(() => {
    setIsVideoPreviewPlaying(false);
  }, [previewVideoUrl]);

  useEffect(() => {
    if (videoPlaybackMode !== 'isolate' || isolatedPlayableFrames.length === 0) {
      return;
    }
    if (!previewVideoFrame || !isolatedPlayableFrames.some((frame) => frame.id === previewVideoFrame.id)) {
      setSelectedFrameId(isolatedPlayableFrames[0].id);
    }
  }, [isolatedPlayableFrames, previewVideoFrame, setSelectedFrameId, videoPlaybackMode]);

  useEffect(() => {
    if (!shouldAutoplayQueuedVideo || !previewVideoUrl) {
      return;
    }
    const timeoutId = window.setTimeout(() => {
      const player = videoPreviewRef.current;
      if (!player) {
        return;
      }
      if (Number.isFinite(player.duration) && player.duration > 0 && player.currentTime >= player.duration - 0.05) {
        player.currentTime = 0;
      }
      void player.play().catch((error) => {
        console.error('Failed to autoplay queued video preview:', error);
      });
      setShouldAutoplayQueuedVideo(false);
    }, 80);
    return () => window.clearTimeout(timeoutId);
  }, [previewVideoUrl, shouldAutoplayQueuedVideo]);

  useEffect(() => {
    if (activeTab === 'cast') {
      preloadMediaWindow(
        cast.slice(castPage.pageStart, castPage.pageStart + ENTITY_PAGE_SIZES.cast * 2).map((entity) => ({
          imageUrl: entity.thumbnailUrl || entity.imageUrl,
        })),
      );
      return;
    }
    if (activeTab === 'locations') {
      preloadMediaWindow(
        locations
          .slice(locationPage.pageStart, locationPage.pageStart + ENTITY_PAGE_SIZES.locations * 2)
          .map((entity) => ({
            imageUrl: entity.thumbnailUrl || entity.imageUrl,
          })),
      );
      return;
    }
    if (activeTab === 'props') {
      preloadMediaWindow(
        props.slice(propPage.pageStart, propPage.pageStart + ENTITY_PAGE_SIZES.props * 2).map((entity) => ({
          imageUrl: entity.thumbnailUrl || entity.imageUrl,
        })),
      );
      return;
    }
    if (activeTab === 'storyboard') {
      preloadMediaWindow(
        storyboardFrames
          .slice(storyboardPageStart, storyboardPageStart + STORYBOARD_PAGE_SIZE * 2)
          .map((frame) => ({
            imageUrl: frame.thumbnailUrl || frame.imageUrl,
          })),
      );
      return;
    }
    if (activeTab === 'video') {
      const anchorIndex = previewVideoFrame
        ? timelineFrames.findIndex((frame) => frame.id === previewVideoFrame.id)
        : timelineFrames.findIndex((frame) => frame.videoUrl);
      const windowStart = Math.max(0, anchorIndex >= 0 ? anchorIndex - 2 : 0);
      const mediaWindow = timelineFrames
        .slice(windowStart, windowStart + 8)
        .map((frame) => ({
          imageUrl: frame.thumbnailUrl || frame.imageUrl,
          posterUrl: frame.thumbnailUrl || frame.imageUrl,
          videoUrl: frame.videoUrl,
        }));
      if (reports.finalExport) {
        mediaWindow.unshift({
          imageUrl: previewPosterUrl,
          posterUrl: previewPosterUrl,
          videoUrl: reports.finalExport,
        });
      }
      preloadMediaWindow(mediaWindow);
    }
  }, [
    activeTab,
    cast,
    castPage.pageStart,
    locationPage.pageStart,
    locations,
    propPage.pageStart,
    props,
    previewPosterUrl,
    previewVideoFrame,
    reports.finalExport,
    storyboardFrames,
    storyboardPageStart,
    timelineFrames,
  ]);

  const renderCollectionPagination = (
    kicker: string,
    currentPage: number,
    pageCount: number,
    pageStart: number,
    pageEnd: number,
    totalCount: number,
    onPageChange: Dispatch<SetStateAction<number>>,
  ) => {
    if (pageCount <= 1) {
      return null;
    }

    return (
      <div className="collection-pagination">
        <button
          type="button"
          className="collection-pagination-btn"
          onClick={() => {
            clearSelection();
            onPageChange((page) => Math.max(0, page - 1));
          }}
          disabled={currentPage === 0}
          aria-label={`Show previous ${kicker.toLowerCase()} page`}
        >
          <ChevronLeft size={14} />
        </button>
        <div className="collection-pagination-copy">
          <span className="collection-pagination-kicker">{kicker}</span>
          <span className="collection-pagination-label">
            {pageStart + 1}-{pageEnd} of {totalCount}
          </span>
        </div>
        <button
          type="button"
          className="collection-pagination-btn"
          onClick={() => {
            clearSelection();
            onPageChange((page) => Math.min(pageCount - 1, page + 1));
          }}
          disabled={currentPage >= pageCount - 1}
          aria-label={`Show next ${kicker.toLowerCase()} page`}
        >
          <ChevronRight size={14} />
        </button>
      </div>
    );
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'complete':
      case 'approved':
        return <Check size={12} style={{ color: 'var(--success)' }} />;
      case 'generating':
        return <Clock size={12} style={{ color: 'var(--accent)' }} />;
      case 'error':
        return <AlertCircle size={12} style={{ color: 'var(--error)' }} />;
      default:
        return null;
    }
  };

  useEffect(() => {
    if (selectedEntity) {
      setEditorTitle(selectedEntity.name || '');
      setEditorDescription(selectedEntity.description || '');
      setEditorLocation('');
      setEditorCharacters('');
      return;
    }
    setEditorTitle('');
    setEditorDescription('');
    setEditorLocation('');
    setEditorCharacters('');
  }, [selectedEntity, selectedScene?.id]);

  useEffect(() => {
    let cancelled = false;
    if (!currentProject || !selectedScene) {
      return;
    }
    void API.graph
      .getNode(currentProject.id, 'scene', selectedScene.id)
      .then((node) => {
        if (cancelled) {
          return;
        }
        setEditorTitle(String(node.scene_heading || node.title || selectedScene.heading || ''));
        setEditorDescription(String(node.emotional_arc || selectedScene.description || ''));
        setEditorLocation(String(node.location_id || selectedScene.location || ''));
        setEditorCharacters(((node.cast_present as string[] | undefined) || selectedScene.characters || []).join(', '));
        const staging = (node.staging_plan || {}) as SceneStagingPlan;
        setSceneStartPositions(formatJsonMap(staging.start?.cast_positions));
        setSceneStartLooking(formatJsonMap(staging.start?.cast_looking_at));
        setSceneStartFacing(formatJsonMap(staging.start?.cast_facing));
        setSceneMidPositions(formatJsonMap(staging.mid?.cast_positions));
        setSceneMidLooking(formatJsonMap(staging.mid?.cast_looking_at));
        setSceneMidFacing(formatJsonMap(staging.mid?.cast_facing));
        setSceneEndPositions(formatJsonMap(staging.end?.cast_positions));
        setSceneEndLooking(formatJsonMap(staging.end?.cast_looking_at));
        setSceneEndFacing(formatJsonMap(staging.end?.cast_facing));
      })
      .catch((error) => {
        console.error('Failed to load scene graph node:', error);
      });
    return () => {
      cancelled = true;
    };
  }, [currentProject, selectedScene]);

  useEffect(() => {
    let cancelled = false;
    if (!currentProject || !selectedFrame) {
      setFrameContext(null);
      setFrameNarrativeBeat('');
      setFrameActionSummary('');
      setFrameCameraFacing('');
      setFrameShot('');
      setFrameAngle('');
      setFrameBlocking('');
      setFrameMovementPath('');
      setFrameReactionTarget('');
      setFrameCastStates([]);
      setFramePropStates([]);
      setFrameLocationStates([]);
      return;
    }
    void API.graph
      .getFrameContext(currentProject.id, selectedFrame.id)
      .then((context) => {
        if (cancelled) {
          return;
        }
        setFrameContext(context);
        setFrameNarrativeBeat(String(context.frame.narrative_beat || context.frame.source_text || ''));
        setFrameActionSummary(String(context.frame.action_summary || ''));
        setFrameCameraFacing(String(context.frame.background?.camera_facing || ''));
        setFrameShot(String(context.frame.composition?.shot || ''));
        setFrameAngle(String(context.frame.composition?.angle || ''));
        setFrameBlocking(String(context.frame.composition?.blocking || ''));
        setFrameMovementPath(String(context.frame.directing?.movement_path || ''));
        setFrameReactionTarget(String(context.frame.directing?.reaction_target || ''));
        setFrameCastStates((context.castStates || []).map((state) => ({ ...state })));
        setFramePropStates((context.propStates || []).map((state) => ({ ...state })));
        setFrameLocationStates((context.locationStates || []).map((state) => ({ ...state })));
      })
      .catch((error) => {
        console.error('Failed to load frame context:', error);
        if (!cancelled) {
          setFrameContext(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [currentProject, selectedFrame]);

  const handleItemClick = (item: SelectedItem, e: React.MouseEvent) => {
    const isShiftClick = e.shiftKey;
    toggleItemSelection(item, isShiftClick);
    
    // Also inject to chat on shift-click
    if (isShiftClick) {
      injectFocusToChat(item);
    }
  };

  const handleDragOver = (e: React.DragEvent, entityId: string) => {
    e.preventDefault();
    e.stopPropagation();
    setDragState({ isDragging: true, entityId });
  };

  const handleDragLeave = (e: React.DragEvent, entityId: string) => {
    e.preventDefault();
    const nextTarget = e.relatedTarget;
    if (nextTarget instanceof Node && e.currentTarget.contains(nextTarget)) {
      return;
    }
    if (dragState.entityId !== entityId) {
      return;
    }
    setDragState({ isDragging: false, entityId: null });
  };

  const handleDrop = (e: React.DragEvent, entityId: string) => {
    e.preventDefault();
    e.stopPropagation();
    setDragState({ isDragging: false, entityId: null });

    const files = e.dataTransfer.files;
    if (files.length > 0 && files[0].type.startsWith('image/')) {
      void handleImageUpload(entityId, files[0]);
    }
  };

  const handleImageUpload = async (entityId: string, file: File) => {
    if (!currentProject) {
      uploadEntityImage(entityId, URL.createObjectURL(file));
      return;
    }
    try {
      const result = await API.entities.uploadImage(currentProject.id, entityId, file);
      uploadEntityImage(entityId, result.imageUrl);
    } catch (error) {
      console.error('Failed to upload entity image:', error);
      uploadEntityImage(entityId, URL.createObjectURL(file));
    }
  };

  const handleFileSelect = (entityId: string) => {
    setUploadTargetEntity(entityId);
    fileInputRef.current?.click();
  };

  const handlePlayVideoPreview = () => {
    const player = videoPreviewRef.current;
    if (!player) {
      return;
    }
    if (player.readyState === 0) {
      player.load();
    }
    if (Number.isFinite(player.duration) && player.duration > 0 && player.currentTime >= player.duration - 0.05) {
      player.currentTime = 0;
    }
    void player.play().catch((error) => {
      console.error('Failed to start video preview:', error);
    });
  };

  const handleVideoPreviewEnded = () => {
    setIsVideoPreviewPlaying(false);
    if (currentPlaybackQueueIndex < 0 || currentPlaybackQueueIndex >= playbackQueue.length - 1) {
      return;
    }
    const nextFrame = playbackQueue[currentPlaybackQueueIndex + 1];
    setSelectedFrameId(nextFrame.id);
    setShouldAutoplayQueuedVideo(true);
  };

  const renderEntityCard = (entity: Entity, typeLabel: string) => {
    const isSelected = isItemSelected(entity.id);
    const isDragOver = dragState.isDragging && dragState.entityId === entity.id;
    const isCastEntity = entity.type === 'cast';
    const isWideEntity = entity.type === 'location';
    const entityImageUrl = entity.thumbnailUrl || entity.imageUrl;
    const summaryText =
      entity.storySummary || entity.description || 'Story placement summary will land here once the entity pack is enriched.';

    return (
      <div 
        key={entity.id} 
        className={`entity-card ${isCastEntity ? 'entity-card-cast' : ''} ${isWideEntity ? 'entity-card-wide' : ''} ${isSelected ? 'is-selected' : ''} ${isDragOver ? 'is-drag-over' : ''}`}
        data-testid={`entity-card-${entity.id}`}
        onClick={(e) => handleItemClick({ type: entity.type, id: entity.id, name: entity.name }, e)}
        style={{
          border: isSelected ? '2px solid var(--success)' : isDragOver ? '2px dashed var(--accent)' : '1px solid var(--border-subtle)',
          boxShadow: isSelected ? '0 0 12px rgba(16, 185, 129, 0.4)' : undefined,
          cursor: 'pointer',
          position: 'relative',
        }}
        onDragOver={(e) => handleDragOver(e, entity.id)}
        onDragLeave={(e) => handleDragLeave(e, entity.id)}
        onDrop={(e) => handleDrop(e, entity.id)}
      >
        <div className={`entity-card-image ${isCastEntity ? 'cast' : ''} ${isWideEntity ? 'wide' : ''}`} style={{ position: 'relative' }}>
          <button
            type="button"
            className="entity-card-upload-btn"
            data-testid={`entity-dropzone-${entity.id}`}
            aria-label={`Upload reference image for ${entity.name}`}
            onClick={(e) => {
              e.stopPropagation();
              handleFileSelect(entity.id);
            }}
          >
            <Upload size={15} />
          </button>
          {entityImageUrl ? (
            <img src={entityImageUrl} alt={entity.name} className={isCastEntity ? 'entity-card-img-cast' : undefined} />
          ) : (
            <div 
              className={`entity-card-empty-state ${isDragOver ? 'is-active' : ''}`}
              style={{ 
                display: 'flex', 
                flexDirection: 'column',
                alignItems: 'center', 
                justifyContent: 'center',
                height: '100%',
                background: isDragOver ? 'var(--accent-dim)' : 'var(--bg-tertiary)',
                gap: '8px',
              }}
              onClick={(e) => {
                e.stopPropagation();
                handleFileSelect(entity.id);
              }}
            >
              {entity.type === 'cast' && <Users size={30} style={{ color: 'var(--text-muted)' }} />}
              {entity.type === 'location' && <MapPin size={30} style={{ color: 'var(--text-muted)' }} />}
              {entity.type === 'prop' && <Package size={30} style={{ color: 'var(--text-muted)' }} />}
              <p className="entity-card-empty-copy">
                Drag and drop reference image or let the system decide in the next step
              </p>
            </div>
          )}

          {isDragOver ? (
            <div
              data-testid={`entity-drop-overlay-${entity.id}`}
              style={{
                position: 'absolute',
                inset: 0,
                background: 'linear-gradient(180deg, rgba(59, 212, 160, 0.14), rgba(59, 212, 160, 0.28))',
                border: '2px dashed var(--accent)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '14px',
                textAlign: 'center',
                fontSize: '12px',
                fontWeight: 600,
                color: 'var(--text-primary)',
                pointerEvents: 'none',
              }}
            >
              Drop reference image
            </div>
          ) : null}
          
          {/* Selection indicator */}
          {isSelected && (
            <div
              style={{
                position: 'absolute',
                top: '8px',
                right: '8px',
                width: '20px',
                height: '20px',
                borderRadius: '50%',
                background: 'var(--success)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Check size={12} style={{ color: 'var(--bg-primary)' }} />
            </div>
          )}

          {entityImageUrl ? (
            <div className="entity-card-overlay">
              <div className="entity-card-overlay-header">
                <span className="entity-card-name entity-card-name-overlay">{entity.name}</span>
                {getStatusIcon(entity.status)}
              </div>
              <span className="entity-card-type entity-card-type-overlay">{typeLabel}</span>
              <p className="entity-card-story-summary entity-card-story-summary-overlay">{summaryText}</p>
            </div>
          ) : null}
        </div>
        {!entityImageUrl ? (
          <div className="entity-card-info">
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '2px' }}>
              <span className="entity-card-name">{entity.name}</span>
              {getStatusIcon(entity.status)}
            </div>
            <span className="entity-card-type">{typeLabel}</span>
            <p className="entity-card-story-summary">{summaryText}</p>
          </div>
        ) : null}
      </div>
    );
  };

  const renderContent = () => {
    switch (activeTab) {
      case 'outline':
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div className="skeleton-viewer">
              {skeletonPlan && skeletonPlan.scenes.length > 0 ? (
                skeletonPlan.scenes.map((scene) => {
                  const isSelected = isItemSelected(scene.id);
                  return (
                    <div
                      key={scene.id}
                      className="skeleton-scene"
                      onClick={(e) => handleItemClick({ type: 'scene', id: scene.id, name: `Scene ${scene.number}` }, e)}
                      style={{
                        border: isSelected ? '2px solid var(--success)' : undefined,
                        borderRadius: isSelected ? '8px' : undefined,
                        padding: isSelected ? '10px 14px' : undefined,
                        background: isSelected ? 'rgba(16, 185, 129, 0.1)' : undefined,
                        cursor: 'pointer',
                      }}
                    >
                      <div className="skeleton-scene-header">
                        <span className="skeleton-scene-number">Scene {scene.number}</span>
                        <span className="skeleton-scene-location">{scene.location}</span>
                        {isSelected && <Check size={14} style={{ color: 'var(--success)', marginLeft: 'auto' }} />}
                      </div>
                      <p className="skeleton-scene-description">{scene.description}</p>
                    </div>
                  );
                })
              ) : skeletonPlan?.markdown ? (
                <pre style={{
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontFamily: 'inherit',
                  fontSize: '13px',
                  lineHeight: 1.7,
                  color: 'var(--text-secondary)',
                  maxHeight: '600px',
                  overflow: 'auto',
                }}>{skeletonPlan.markdown}</pre>
              ) : (
                <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-secondary)' }}>
                  <p>No outline generated yet.</p>
                  <p style={{ fontSize: '13px', marginTop: '8px' }}>
                    The agent will create a scene breakdown based on your concept.
                  </p>
                </div>
              )}
            </div>
          </div>
        );

      case 'script':
        return (
          <div style={{ fontFamily: 'IBM Plex Serif, serif', lineHeight: 1.7 }}>
            <div style={{ 
              background: 'var(--bg-secondary)', 
              padding: '20px', 
              borderRadius: '12px',
              border: '1px solid var(--border-subtle)'
            }}>
              <p style={{ 
                textAlign: 'center', 
                textTransform: 'uppercase',
                letterSpacing: '0.1em',
                fontSize: '12px',
                marginBottom: '24px',
                color: 'var(--text-muted)'
              }}>
                SCRIPT
              </p>
              {scriptText ? (
                <pre
                  style={{
                    margin: 0,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    fontFamily: 'IBM Plex Serif, serif',
                    fontSize: '13px',
                    color: 'var(--text-primary)',
                  }}
                >
                  {scriptText}
                </pre>
              ) : (
                <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-secondary)' }}>
                  <p>No script text available yet.</p>
                  <p style={{ fontSize: '13px', marginTop: '8px' }}>
                    Morpheus will populate this once creative output is generated for the selected project.
                  </p>
                </div>
              )}
            </div>
          </div>
        );

      case 'cast':
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {renderCollectionPagination(
              'Cast page',
              castPage.currentPage,
              castPage.pageCount,
              castPage.pageStart,
              castPage.pageEnd,
              cast.length,
              (next) => setEntityPages((pages) => ({ ...pages, cast: typeof next === 'function' ? next(pages.cast) : next })),
            )}
            <div className="entity-grid entity-grid-cast">
              {castPage.items.map((member) => renderEntityCard(member, 'Cast'))}
            </div>
          </div>
        );

      case 'locations':
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {renderCollectionPagination(
              'Location page',
              locationPage.currentPage,
              locationPage.pageCount,
              locationPage.pageStart,
              locationPage.pageEnd,
              locations.length,
              (next) => setEntityPages((pages) => ({ ...pages, locations: typeof next === 'function' ? next(pages.locations) : next })),
            )}
            <div className="entity-grid entity-grid-wide">
              {locationPage.items.map((location) => renderEntityCard(location, 'Location'))}
            </div>
          </div>
        );

      case 'props':
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {renderCollectionPagination(
              'Prop page',
              propPage.currentPage,
              propPage.pageCount,
              propPage.pageStart,
              propPage.pageEnd,
              props.length,
              (next) => setEntityPages((pages) => ({ ...pages, props: typeof next === 'function' ? next(pages.props) : next })),
            )}
            <div className="entity-grid">
              {props.length > 0 ? (
                propPage.items.map((prop) => renderEntityCard(prop, 'Prop'))
              ) : (
                <div style={{ 
                  gridColumn: '1 / -1',
                  textAlign: 'center', 
                  padding: '40px 20px', 
                  color: 'var(--text-secondary)',
                  background: 'var(--bg-secondary)',
                  borderRadius: '12px',
                  border: '1px dashed var(--border-subtle)'
                }}>
                  <Package size={28} style={{ margin: '0 auto 12px', opacity: 0.5 }} />
                  <p>No props generated yet.</p>
                </div>
              )}
            </div>
          </div>
        );

      case 'storyboard':
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {storyboardPageCount > 1 ? (
              <div className="collection-pagination">
                <button
                  type="button"
                  className="collection-pagination-btn"
                  onClick={() => {
                    clearSelection();
                    setStoryboardPage(Math.max(0, currentStoryboardPage - 1));
                  }}
                  disabled={currentStoryboardPage === 0}
                  aria-label="Show previous storyboard page"
                >
                  <ChevronLeft size={14} />
                </button>
                <div className="collection-pagination-copy">
                  <span className="collection-pagination-kicker">Storyboard page</span>
                  <span className="collection-pagination-label">
                    {currentStoryboardPage + 1} / {storyboardPageCount}
                  </span>
                </div>
                <button
                  type="button"
                  className="collection-pagination-btn"
                  onClick={() => {
                    clearSelection();
                    setStoryboardPage(Math.min(storyboardPageCount - 1, currentStoryboardPage + 1));
                  }}
                  disabled={currentStoryboardPage >= storyboardPageCount - 1}
                  aria-label="Show next storyboard page"
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            ) : null}
            <div className="storyboard-grid">
              {storyboardPageFrames.map((frame) => {
                const isSelected = isItemSelected(frame.id);
                const frameImageUrl = frame.thumbnailUrl || frame.imageUrl;
                return (
                  <div 
                    key={frame.id} 
                    className="storyboard-panel"
                    onClick={(e) => handleItemClick({ type: 'storyboard', id: frame.id, name: `Storyboard ${frame.sequence}` }, e)}
                    style={{
                      border: isSelected ? '3px solid var(--success)' : undefined,
                      boxShadow: isSelected ? '0 0 16px rgba(16, 185, 129, 0.4)' : undefined,
                      cursor: 'pointer',
                      position: 'relative',
                    }}
                  >
                    {frameImageUrl ? (
                      <img src={frameImageUrl} alt={frame.description} loading="lazy" />
                    ) : (
                      <div className="storyboard-panel-text">
                        <div className="storyboard-panel-meta">
                          <span className="storyboard-panel-sequence">#{frame.sequence}</span>
                          <span className="storyboard-panel-shot">{frame.shotType.replace(/_/g, ' ')}</span>
                        </div>
                        <p className="storyboard-panel-copy">{frame.description}</p>
                      </div>
                    )}
                    {isSelected && (
                      <div
                        style={{
                          position: 'absolute',
                          top: '8px',
                          right: '8px',
                          width: '24px',
                          height: '24px',
                          borderRadius: '50%',
                          background: 'var(--success)',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                        }}
                      >
                        <Check size={14} style={{ color: 'var(--bg-primary)' }} />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );

      case 'video':
        return (
          <div className="video-panel-shell">
            <div className={`video-panel-stage ${previewVideoUrl ? 'has-video' : 'is-empty'}`.trim()}>
              {previewVideoUrl ? (
                <video
                  key={previewVideoUrl}
                  ref={videoPreviewRef}
                  className="video-panel-player"
                  src={previewVideoUrl}
                  poster={previewPosterUrl}
                  preload="auto"
                  controls
                  playsInline
                  onPlay={() => setIsVideoPreviewPlaying(true)}
                  onPause={() => setIsVideoPreviewPlaying(false)}
                  onEnded={handleVideoPreviewEnded}
                />
              ) : (
                <div className="video-panel-empty-state">
                  <div className="video-panel-empty-icon">
                    <Play size={30} />
                  </div>
                  <div className="video-panel-empty-copy">
                    <h3>Waiting for generated clips</h3>
                    <p>The panel will auto-populate as soon as clips are generated.</p>
                  </div>
                </div>
              )}

              {previewVideoUrl ? (
                <>
                  <div className="video-panel-overlay">
                    <div className="video-panel-copy">
                      <span className="video-panel-kicker">{previewVideoLabel}</span>
                      <strong className="video-panel-title">{previewVideoTitle}</strong>
                    </div>
                  <div className="video-panel-actions">
                    <button
                      type="button"
                      className={`video-panel-mode-toggle ${videoPlaybackMode === 'playthrough' ? 'is-playthrough' : 'is-isolate'}`.trim()}
                      aria-label={
                        videoPlaybackMode === 'playthrough'
                          ? 'Switch video preview to isolate mode'
                          : 'Switch video preview to play-through mode'
                      }
                      title={
                        videoPlaybackMode === 'playthrough'
                          ? 'Play through the full clip queue'
                          : 'Isolate playback to the selected clips'
                      }
                      onClick={() => setVideoPlaybackMode((mode) => (mode === 'playthrough' ? 'isolate' : 'playthrough'))}
                    >
                      <span className="video-panel-mode-track" aria-hidden="true">
                        <span className="video-panel-mode-slot video-panel-mode-slot-left">
                          <Video size={14} />
                        </span>
                        <span className="video-panel-mode-slot video-panel-mode-slot-center">
                          <span className="video-panel-mode-center-primary">
                            <Video size={14} />
                          </span>
                          <span className="video-panel-mode-center-secondary">
                            <ArrowRight size={14} />
                          </span>
                        </span>
                        <span className="video-panel-mode-slot video-panel-mode-slot-right">
                          <Video size={14} />
                        </span>
                      </span>
                    </button>
                    <button
                      type="button"
                      className="video-panel-action is-secondary"
                      onClick={() => setIsExportWizardOpen(true)}
                    >
                      <Download size={15} />
                      Export
                    </button>
                    </div>
                  </div>
                  {!isVideoPreviewPlaying ? (
                    <button
                      type="button"
                      className="video-panel-playhead"
                      aria-label="Play video preview"
                      title="Play video preview"
                      onClick={handlePlayVideoPreview}
                    >
                      <Play size={24} />
                    </button>
                  ) : null}
                </>
              ) : null}

            </div>
          </div>
        );

      default:
        return null;
    }
  };

  const handleSaveSelection = async () => {
    if (!currentProject || !selectedItem) {
      return;
    }
    setIsSavingSelection(true);
    try {
      if (selectedFrame && frameContext) {
        let snapshot = await API.graph.updateNode(currentProject.id, 'frame', selectedFrame.id, {
          narrative_beat: frameNarrativeBeat,
          source_text: frameNarrativeBeat,
          action_summary: frameActionSummary,
          background: {
            ...(frameContext.frame.background || {}),
            camera_facing: frameCameraFacing || null,
          },
          composition: {
            ...(frameContext.frame.composition || {}),
            shot: frameShot || null,
            angle: frameAngle || null,
            blocking: frameBlocking || null,
          },
          directing: {
            ...(frameContext.frame.directing || {}),
            movement_path: frameMovementPath || null,
            reaction_target: frameReactionTarget || null,
          },
        });
        for (const castState of frameCastStates) {
          const castStateId = `${castState.cast_id}@${selectedFrame.id}`;
          snapshot = await API.graph.updateNode(currentProject.id, 'cast_frame_state', castStateId, {
            screen_position: castState.screen_position || null,
            looking_at: castState.looking_at || null,
            facing_direction: castState.facing_direction || null,
            spatial_position: castState.spatial_position || null,
          });
        }
        for (const propState of framePropStates) {
          const propStateId = `${propState.prop_id}@${selectedFrame.id}`;
          snapshot = await API.graph.updateNode(currentProject.id, 'prop_frame_state', propStateId, {
            condition: propState.condition || null,
            condition_detail: propState.condition_detail || null,
            holder_cast_id: propState.holder_cast_id || null,
            spatial_position: propState.spatial_position || null,
            visibility: propState.visibility || null,
            frame_role: propState.frame_role || null,
          });
        }
        for (const locationState of frameLocationStates) {
          const locationStateId = `${locationState.location_id}@${selectedFrame.id}`;
          snapshot = await API.graph.updateNode(currentProject.id, 'location_frame_state', locationStateId, {
            condition_modifiers: locationState.condition_modifiers || [],
            atmosphere_override: locationState.atmosphere_override || null,
            lighting_override: locationState.lighting_override || null,
            damage_level: locationState.damage_level || null,
          });
        }
        hydrateWorkspace(snapshot);
      } else if (selectedScene) {
        const snapshot = await API.graph.updateNode(currentProject.id, 'scene', selectedScene.id, {
          scene_heading: editorTitle,
          title: editorTitle,
          emotional_arc: editorDescription,
          location_id: editorLocation,
          cast_present: editorCharacters
            .split(',')
            .map((item) => item.trim())
            .filter(Boolean),
          staging_plan: {
            start: {
              cast_positions: parseJsonMap(sceneStartPositions),
              cast_looking_at: parseJsonMap(sceneStartLooking),
              cast_facing: parseJsonMap(sceneStartFacing),
            },
            mid: {
              cast_positions: parseJsonMap(sceneMidPositions),
              cast_looking_at: parseJsonMap(sceneMidLooking),
              cast_facing: parseJsonMap(sceneMidFacing),
            },
            end: {
              cast_positions: parseJsonMap(sceneEndPositions),
              cast_looking_at: parseJsonMap(sceneEndLooking),
              cast_facing: parseJsonMap(sceneEndFacing),
            },
          },
        });
        hydrateWorkspace(snapshot);
      } else if (selectedEntity) {
        const nodeType = selectedEntity.type;
        const updates: Record<string, unknown> = {
          name: editorTitle,
          description: editorDescription,
        };
        if (nodeType === 'cast') {
          updates.display_name = editorTitle;
        }
        const snapshot = await API.graph.updateNode(currentProject.id, nodeType, selectedEntity.id, updates);
        hydrateWorkspace(snapshot);
      }
    } catch (error) {
      console.error('Failed to save graph selection:', error);
    } finally {
      setIsSavingSelection(false);
    }
  };

  const inputStyle: CSSProperties = {
    background: 'var(--bg-primary)',
    border: '1px solid var(--border-subtle)',
    borderRadius: '8px',
    padding: '10px 12px',
    color: 'var(--text-primary)',
    fontSize: '12px',
  };

  const textAreaStyle: CSSProperties = {
    ...inputStyle,
    resize: 'vertical',
  };

  const updateFrameCastState = (
    index: number,
    field: keyof GraphCastFrameState,
    value: string,
  ) => {
    setFrameCastStates((current) =>
      current.map((state, currentIndex) =>
        currentIndex === index
          ? {
              ...state,
              [field]: value || null,
            }
          : state,
      ),
    );
  };

  const updateFramePropState = (
    index: number,
    field: keyof GraphPropFrameState,
    value: string,
  ) => {
    setFramePropStates((current) =>
      current.map((state, currentIndex) =>
        currentIndex === index
          ? {
              ...state,
              [field]: value || null,
            }
          : state,
      ),
    );
  };

  const updateFrameLocationState = (
    index: number,
    field: keyof GraphLocationFrameState,
    value: string | string[],
  ) => {
    setFrameLocationStates((current) =>
      current.map((state, currentIndex) =>
        currentIndex === index
          ? {
              ...state,
              [field]: Array.isArray(value) ? value : value || null,
            }
          : state,
      ),
    );
  };

  const sceneStageEditors: Array<{
    label: string;
    positions: string;
    setPositions: Dispatch<SetStateAction<string>>;
    looking: string;
    setLooking: Dispatch<SetStateAction<string>>;
    facing: string;
    setFacing: Dispatch<SetStateAction<string>>;
  }> = [
    {
      label: 'Start',
      positions: sceneStartPositions,
      setPositions: setSceneStartPositions,
      looking: sceneStartLooking,
      setLooking: setSceneStartLooking,
      facing: sceneStartFacing,
      setFacing: setSceneStartFacing,
    },
    {
      label: 'Mid',
      positions: sceneMidPositions,
      setPositions: setSceneMidPositions,
      looking: sceneMidLooking,
      setLooking: setSceneMidLooking,
      facing: sceneMidFacing,
      setFacing: setSceneMidFacing,
    },
    {
      label: 'End',
      positions: sceneEndPositions,
      setPositions: setSceneEndPositions,
      looking: sceneEndLooking,
      setLooking: setSceneEndLooking,
      facing: sceneEndFacing,
      setFacing: setSceneEndFacing,
    },
  ];

  return (
    <div className="detail-panel" data-testid="detail-panel">
      {/* Hidden file input for uploads */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file && uploadTargetEntity) {
            void handleImageUpload(uploadTargetEntity, file);
          }
          setUploadTargetEntity(null);
        }}
      />

      {/* Selection info bar */}
      {selectedItems.length > 0 && (
        <div className="detail-selection-bar">
          <span className="detail-selection-copy">
            {selectedItems.length} item{selectedItems.length > 1 ? 's' : ''} selected
          </span>
          <button
            type="button"
            onClick={clearSelection}
            className="detail-selection-clear"
          >
            Clear
          </button>
        </div>
      )}

      {selectedItem && (selectedScene || selectedEntity || selectedFrame) && (
        <div className="detail-editor-bar">
          <div className="detail-editor-header">
            <div>
              <div className="detail-editor-title">
                Edit selected {selectedFrame ? 'frame' : selectedScene ? 'scene' : selectedEntity?.type}
              </div>
              <div className="detail-editor-subtitle">
                This writes directly into the project graph.
              </div>
            </div>
            <button
              className="btn-accent"
              onClick={() => void handleSaveSelection()}
              disabled={isSavingSelection}
              type="button"
            >
              {isSavingSelection ? 'Saving...' : 'Save'}
            </button>
          </div>
          {selectedFrame ? (
            <>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
                  gap: '8px',
                  fontSize: '10px',
                  color: 'var(--text-secondary)',
                }}
              >
                <div>
                  <div style={{ color: 'var(--text-muted)', marginBottom: '2px' }}>Scene</div>
                  <div>{frameContext?.scene?.scene_heading || frameContext?.scene?.title || frameContext?.frame.scene_id || 'Unassigned'}</div>
                </div>
                <div>
                  <div style={{ color: 'var(--text-muted)', marginBottom: '2px' }}>Dialogue</div>
                  <div>{frameContext?.dialogue?.length ? `${frameContext.dialogue.length} linked line(s)` : 'None linked'}</div>
                </div>
              </div>
              <textarea
                value={frameNarrativeBeat}
                onChange={(e) => setFrameNarrativeBeat(e.target.value)}
                placeholder="Narrative beat / source text"
                rows={3}
                style={textAreaStyle}
              />
              <textarea
                value={frameActionSummary}
                onChange={(e) => setFrameActionSummary(e.target.value)}
                placeholder="Action summary"
                rows={3}
                style={textAreaStyle}
              />
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: '8px' }}>
                <input
                  value={frameCameraFacing}
                  onChange={(e) => setFrameCameraFacing(e.target.value)}
                  placeholder="Camera facing"
                  style={inputStyle}
                />
                <input
                  value={frameShot}
                  onChange={(e) => setFrameShot(e.target.value)}
                  placeholder="Shot"
                  style={inputStyle}
                />
                <input
                  value={frameAngle}
                  onChange={(e) => setFrameAngle(e.target.value)}
                  placeholder="Angle"
                  style={inputStyle}
                />
              </div>
              <textarea
                value={frameBlocking}
                onChange={(e) => setFrameBlocking(e.target.value)}
                placeholder="Blocking"
                rows={2}
                style={textAreaStyle}
              />
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '8px' }}>
                <input
                  value={frameMovementPath}
                  onChange={(e) => setFrameMovementPath(e.target.value)}
                  placeholder="Movement path"
                  style={inputStyle}
                />
                <input
                  value={frameReactionTarget}
                  onChange={(e) => setFrameReactionTarget(e.target.value)}
                  placeholder="Reaction target"
                  style={inputStyle}
                />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-primary)' }}>
                  Cast state and eyelines
                </div>
                {frameCastStates.length > 0 ? (
                  frameCastStates.map((castState, index) => (
                    <div
                      key={`${castState.cast_id}-${index}`}
                      style={{
                        display: 'grid',
                        gridTemplateColumns: '1.2fr 1fr 1fr 1fr 1fr',
                        gap: '8px',
                        padding: '10px',
                        background: 'var(--bg-primary)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: '8px',
                      }}
                    >
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                        <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>Cast</span>
                        <span style={{ fontSize: '12px', color: 'var(--text-primary)', fontWeight: 500 }}>
                          {castState.cast_id}
                        </span>
                      </div>
                      <input
                        value={castState.screen_position || ''}
                        onChange={(e) => updateFrameCastState(index, 'screen_position', e.target.value)}
                        placeholder="Screen pos"
                        style={inputStyle}
                      />
                      <input
                        value={castState.looking_at || ''}
                        onChange={(e) => updateFrameCastState(index, 'looking_at', e.target.value)}
                        placeholder="Looking at"
                        style={inputStyle}
                      />
                      <input
                        value={castState.facing_direction || ''}
                        onChange={(e) => updateFrameCastState(index, 'facing_direction', e.target.value)}
                        placeholder="Facing"
                        style={inputStyle}
                      />
                      <input
                        value={castState.spatial_position || ''}
                        onChange={(e) => updateFrameCastState(index, 'spatial_position', e.target.value)}
                        placeholder="Spatial pos"
                        style={inputStyle}
                      />
                    </div>
                  ))
                ) : (
                  <div
                    style={{
                      padding: '10px 12px',
                      borderRadius: '8px',
                      border: '1px dashed var(--border-subtle)',
                      color: 'var(--text-secondary)',
                      fontSize: '11px',
                    }}
                  >
                    No cast state records are linked to this frame yet.
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-primary)' }}>
                  Prop anchors
                </div>
                {framePropStates.length > 0 ? (
                  framePropStates.map((propState, index) => (
                    <div
                      key={`${propState.prop_id}-${index}`}
                      style={{
                        display: 'grid',
                        gridTemplateColumns: '1.1fr 1fr 1fr 1fr 1fr 1fr',
                        gap: '8px',
                        padding: '10px',
                        background: 'var(--bg-primary)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: '8px',
                      }}
                    >
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                        <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>Prop</span>
                        <span style={{ fontSize: '12px', color: 'var(--text-primary)', fontWeight: 500 }}>
                          {propState.prop_id}
                        </span>
                      </div>
                      <input
                        value={propState.condition || ''}
                        onChange={(e) => updateFramePropState(index, 'condition', e.target.value)}
                        placeholder="Condition"
                        style={inputStyle}
                      />
                      <input
                        value={propState.holder_cast_id || ''}
                        onChange={(e) => updateFramePropState(index, 'holder_cast_id', e.target.value)}
                        placeholder="Holder cast"
                        style={inputStyle}
                      />
                      <input
                        value={propState.spatial_position || ''}
                        onChange={(e) => updateFramePropState(index, 'spatial_position', e.target.value)}
                        placeholder="Spatial pos"
                        style={inputStyle}
                      />
                      <input
                        value={propState.visibility || ''}
                        onChange={(e) => updateFramePropState(index, 'visibility', e.target.value)}
                        placeholder="Visibility"
                        style={inputStyle}
                      />
                      <input
                        value={propState.frame_role || ''}
                        onChange={(e) => updateFramePropState(index, 'frame_role', e.target.value)}
                        placeholder="Frame role"
                        style={inputStyle}
                      />
                    </div>
                  ))
                ) : (
                  <div
                    style={{
                      padding: '10px 12px',
                      borderRadius: '8px',
                      border: '1px dashed var(--border-subtle)',
                      color: 'var(--text-secondary)',
                      fontSize: '11px',
                    }}
                  >
                    No prop frame-state records are linked to this frame yet.
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-primary)' }}>
                  Location state
                </div>
                {frameLocationStates.length > 0 ? (
                  frameLocationStates.map((locationState, index) => (
                    <div
                      key={`${locationState.location_id}-${index}`}
                      style={{
                        display: 'grid',
                        gridTemplateColumns: '1.1fr 1fr 1fr 1fr 1fr',
                        gap: '8px',
                        padding: '10px',
                        background: 'var(--bg-primary)',
                        border: '1px solid var(--border-subtle)',
                        borderRadius: '8px',
                      }}
                    >
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                        <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>Location</span>
                        <span style={{ fontSize: '12px', color: 'var(--text-primary)', fontWeight: 500 }}>
                          {locationState.location_id}
                        </span>
                      </div>
                      <input
                        value={(locationState.condition_modifiers || []).join(', ')}
                        onChange={(e) =>
                          updateFrameLocationState(
                            index,
                            'condition_modifiers',
                            e.target.value
                              .split(',')
                              .map((item) => item.trim())
                              .filter(Boolean),
                          )
                        }
                        placeholder="Condition modifiers"
                        style={inputStyle}
                      />
                      <input
                        value={locationState.atmosphere_override || ''}
                        onChange={(e) => updateFrameLocationState(index, 'atmosphere_override', e.target.value)}
                        placeholder="Atmosphere"
                        style={inputStyle}
                      />
                      <input
                        value={locationState.lighting_override || ''}
                        onChange={(e) => updateFrameLocationState(index, 'lighting_override', e.target.value)}
                        placeholder="Lighting"
                        style={inputStyle}
                      />
                      <input
                        value={locationState.damage_level || ''}
                        onChange={(e) => updateFrameLocationState(index, 'damage_level', e.target.value)}
                        placeholder="Damage"
                        style={inputStyle}
                      />
                    </div>
                  ))
                ) : (
                  <div
                    style={{
                      padding: '10px 12px',
                      borderRadius: '8px',
                      border: '1px dashed var(--border-subtle)',
                      color: 'var(--text-secondary)',
                      fontSize: '11px',
                    }}
                  >
                    No location state overrides are linked to this frame yet.
                  </div>
                )}
              </div>
            </>
          ) : selectedScene ? (
            <>
              <input
                value={editorTitle}
                onChange={(e) => setEditorTitle(e.target.value)}
                placeholder="Scene heading"
                style={inputStyle}
              />
              <textarea
                value={editorDescription}
                onChange={(e) => setEditorDescription(e.target.value)}
                placeholder="Scene description / emotional arc"
                rows={3}
                style={textAreaStyle}
              />
              <input
                value={editorLocation}
                onChange={(e) => setEditorLocation(e.target.value)}
                placeholder="Location id"
                style={inputStyle}
              />
              <input
                value={editorCharacters}
                onChange={(e) => setEditorCharacters(e.target.value)}
                placeholder="Cast ids, comma separated"
                style={inputStyle}
              />
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {sceneStageEditors.map(({ label, positions, setPositions, looking, setLooking, facing, setFacing }) => (
                  <div
                    key={label}
                    style={{
                      padding: '10px',
                      background: 'var(--bg-primary)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: '8px',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '8px',
                    }}
                  >
                    <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-primary)' }}>
                      {label} staging
                    </div>
                    <textarea
                      value={positions}
                      onChange={(e) => setPositions(e.target.value)}
                      placeholder='{"cast_id":"screen_position"}'
                      rows={3}
                      style={textAreaStyle}
                    />
                    <textarea
                      value={looking}
                      onChange={(e) => setLooking(e.target.value)}
                      placeholder='{"cast_id":"looking_at"}'
                      rows={3}
                      style={textAreaStyle}
                    />
                    <textarea
                      value={facing}
                      onChange={(e) => setFacing(e.target.value)}
                      placeholder='{"cast_id":"facing_direction"}'
                      rows={3}
                      style={textAreaStyle}
                    />
                  </div>
                ))}
              </div>
            </>
          ) : selectedEntity ? (
            <>
              <input
                value={editorTitle}
                onChange={(e) => setEditorTitle(e.target.value)}
                placeholder="Name"
                style={inputStyle}
              />
              <textarea
                value={editorDescription}
                onChange={(e) => setEditorDescription(e.target.value)}
                placeholder="Description"
                rows={3}
                style={textAreaStyle}
              />
            </>
          ) : null}
        </div>
      )}

      {/* Just tabs - no header bar */}
      <div className="detail-tabs">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              className={`tab ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <Icon size={12} style={{ marginRight: '4px', display: 'inline' }} />
              {tab.label}
            </button>
          );
        })}
      </div>

      <div className={`detail-content ${activeTab === 'video' ? 'detail-content-video' : ''}`.trim()}>
        {renderContent()}
      </div>
    </div>
  );
}
