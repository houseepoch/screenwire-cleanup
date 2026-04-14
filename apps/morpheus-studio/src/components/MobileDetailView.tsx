import { useState } from 'react';
import { useMorpheusStore } from '../store';
import { useLongPress } from '../hooks/useLongPress';
import { 
  FileText, 
  Scroll, 
  Users, 
  MapPin, 
  Package, 
  LayoutGrid, 
  Play,
  MessageSquare,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import type { TabType, Entity, Scene, StoryboardFrame } from '../types';

const tabs: { id: TabType; label: string; icon: React.ElementType }[] = [
  { id: 'outline', label: 'Outline', icon: FileText },
  { id: 'script', label: 'Script', icon: Scroll },
  { id: 'cast', label: 'Cast', icon: Users },
  { id: 'locations', label: 'Locs', icon: MapPin },
  { id: 'props', label: 'Props', icon: Package },
  { id: 'storyboard', label: 'Board', icon: LayoutGrid },
  { id: 'video', label: 'Video', icon: Play },
];

interface FocusTarget {
  type: string;
  id: string;
  name: string;
}

const MOBILE_PAGE_SIZES = {
  cast: 4,
  locations: 4,
  props: 4,
  storyboard: 6,
} as const;

type MobilePagedTab = keyof typeof MOBILE_PAGE_SIZES;

function HoldToFocusOverlay({ isPressing }: { isPressing: boolean }) {
  if (!isPressing) {
    return null;
  }

  return (
    <div style={{
      position: 'absolute',
      inset: 0,
      background: 'rgba(16, 185, 129, 0.2)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    }}>
      <span style={{
        padding: '6px 12px',
        background: 'var(--success)',
        borderRadius: '12px',
        fontSize: '12px',
        fontWeight: 500,
        color: 'var(--bg-primary)',
      }}>
        Hold to focus
      </span>
    </div>
  );
}

function getFocusBorder(isHighlighted: boolean, isPressing: boolean) {
  return isHighlighted || isPressing
    ? '2px solid var(--success)'
    : '1px solid var(--border-subtle)';
}

function OutlineSceneCard({
  scene,
  isHighlighted,
  onFocus,
}: {
  scene: Scene;
  isHighlighted: boolean;
  onFocus: (item: FocusTarget) => void;
}) {
  const { handlers, isPressing } = useLongPress({
    onLongPress: () => onFocus({ type: 'scene', id: scene.id, name: `Scene ${scene.number}` }),
    ms: 600,
  });

  return (
    <div
      {...handlers}
      style={{
        padding: '14px',
        background: 'var(--bg-secondary)',
        borderRadius: '10px',
        border: getFocusBorder(isHighlighted, isPressing),
        position: 'relative',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
        <span style={{ fontSize: '12px', color: 'var(--accent)', fontWeight: 600 }}>
          Scene {scene.number}
        </span>
        <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
          {scene.location}
        </span>
      </div>
      <p style={{ fontSize: '13px', color: 'var(--text-primary)', lineHeight: 1.5 }}>
        {scene.description}
      </p>
      <HoldToFocusOverlay isPressing={isPressing} />
    </div>
  );
}

function EntityFocusCard({
  entity,
  isHighlighted,
  onFocus,
  icon: Icon,
  aspectRatio,
}: {
  entity: Entity;
  isHighlighted: boolean;
  onFocus: (item: FocusTarget) => void;
  icon: React.ElementType;
  aspectRatio: string;
}) {
  const { handlers, isPressing } = useLongPress({
    onLongPress: () => onFocus({ type: 'entity', id: entity.id, name: entity.name }),
    ms: 600,
  });

  return (
    <div
      {...handlers}
      style={{
        position: 'relative',
        borderRadius: '10px',
        overflow: 'hidden',
        border: getFocusBorder(isHighlighted, isPressing),
      }}
    >
      <div style={{ aspectRatio }}>
        {entity.thumbnailUrl || entity.imageUrl ? (
          <img src={entity.thumbnailUrl || entity.imageUrl} alt={entity.name} loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        ) : (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            height: '100%',
            background: 'var(--bg-tertiary)',
          }}>
            <Icon size={28} style={{ color: 'var(--text-muted)' }} />
          </div>
        )}
      </div>
      <div style={{
        position: 'absolute',
        bottom: 0,
        left: 0,
        right: 0,
        padding: '10px',
        background: 'linear-gradient(to top, rgba(0,0,0,0.9), transparent)',
      }}>
        <span style={{ fontSize: '13px', fontWeight: 500 }}>{entity.name}</span>
      </div>
      <HoldToFocusOverlay isPressing={isPressing} />
    </div>
  );
}

function StoryboardFocusCard({
  frame,
  isHighlighted,
  onFocus,
}: {
  frame: StoryboardFrame;
  isHighlighted: boolean;
  onFocus: (item: FocusTarget) => void;
}) {
  const { handlers, isPressing } = useLongPress({
    onLongPress: () => onFocus({ type: 'storyboard', id: frame.id, name: `Storyboard ${frame.sequence}` }),
    ms: 600,
  });

  return (
    <div
      {...handlers}
      style={{
        position: 'relative',
        borderRadius: '10px',
        overflow: 'hidden',
        border: getFocusBorder(isHighlighted, isPressing),
      }}
    >
      <div style={{ aspectRatio: '16/10' }}>
        <img src={frame.thumbnailUrl || frame.imageUrl} alt={frame.description} loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
      </div>
      <div style={{
        position: 'absolute',
        bottom: 0,
        left: 0,
        right: 0,
        padding: '10px',
        background: 'linear-gradient(to top, rgba(0,0,0,0.9), transparent)',
      }}>
        <span style={{ fontSize: '12px' }}>{frame.shotType}</span>
      </div>
      <HoldToFocusOverlay isPressing={isPressing} />
    </div>
  );
}

export function MobileDetailView() {
  const { 
    entities, 
    storyboardFrames,
    skeletonPlan,
    scriptText,
    setMobileView,
    injectFocusToChat,
    highlightedItem,
  } = useMorpheusStore();

  const [activeTabState, setActiveTabState] = useState<TabType>('outline');
  const [collectionPages, setCollectionPages] = useState<Record<MobilePagedTab, number>>({
    cast: 0,
    locations: 0,
    props: 0,
    storyboard: 0,
  });

  const cast = entities.filter((e): e is Entity & { type: 'cast' } => e.type === 'cast');
  const locations = entities.filter((e): e is Entity & { type: 'location' } => e.type === 'location');
  const props = entities.filter((e): e is Entity & { type: 'prop' } => e.type === 'prop');

  const handleLongPress = (item: FocusTarget) => {
    injectFocusToChat(item);
    setMobileView('chat');
  };

  const getPagedItems = <T extends Entity | StoryboardFrame>(tabKey: MobilePagedTab, items: T[]) => {
    const pageSize = MOBILE_PAGE_SIZES[tabKey];
    const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
    const currentPage = Math.min(collectionPages[tabKey], pageCount - 1);
    const start = currentPage * pageSize;
    return {
      currentPage,
      pageCount,
      start,
      end: Math.min(items.length, start + pageSize),
      items: items.slice(start, start + pageSize),
    };
  };

  const castPage = getPagedItems('cast', cast);
  const locationPage = getPagedItems('locations', locations);
  const propPage = getPagedItems('props', props);
  const storyboardPage = getPagedItems('storyboard', storyboardFrames);

  const renderPagination = (
    tabKey: MobilePagedTab,
    label: string,
    currentPage: number,
    pageCount: number,
    start: number,
    end: number,
    total: number,
  ) => {
    if (pageCount <= 1) {
      return null;
    }

    return (
      <div className="collection-pagination collection-pagination-compact" style={{ margin: '0 auto 12px auto' }}>
        <button
          type="button"
          className="collection-pagination-btn"
          onClick={() => setCollectionPages((pages) => ({ ...pages, [tabKey]: Math.max(0, pages[tabKey] - 1) }))}
          disabled={currentPage === 0}
          aria-label={`Show previous ${label.toLowerCase()} page`}
        >
          <ChevronLeft size={14} />
        </button>
        <div className="collection-pagination-copy">
          <span className="collection-pagination-kicker">{label}</span>
          <span className="collection-pagination-label">{start + 1}-{end} of {total}</span>
        </div>
        <button
          type="button"
          className="collection-pagination-btn"
          onClick={() => setCollectionPages((pages) => ({ ...pages, [tabKey]: Math.min(pageCount - 1, pages[tabKey] + 1) }))}
          disabled={currentPage >= pageCount - 1}
          aria-label={`Show next ${label.toLowerCase()} page`}
        >
          <ChevronRight size={14} />
        </button>
      </div>
    );
  };

  const renderContent = () => {
    switch (activeTabState) {
      case 'outline':
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {skeletonPlan ? (
              skeletonPlan.scenes.map((scene) => (
                <OutlineSceneCard
                  key={scene.id}
                  scene={scene}
                  isHighlighted={highlightedItem?.id === scene.id}
                  onFocus={handleLongPress}
                />
              ))
            ) : (
              <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-secondary)' }}>
                <p>No outline generated yet.</p>
              </div>
            )}
          </div>
        );

      case 'cast':
        return (
          <div>
            {renderPagination('cast', 'Cast page', castPage.currentPage, castPage.pageCount, castPage.start, castPage.end, cast.length)}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
              {castPage.items.map((member) => (
                <EntityFocusCard
                  key={member.id}
                  entity={member}
                  isHighlighted={highlightedItem?.id === member.id}
                  onFocus={handleLongPress}
                  icon={Users}
                  aspectRatio="3/4"
                />
              ))}
            </div>
          </div>
        );

      case 'locations':
        return (
          <div>
            {renderPagination('locations', 'Location page', locationPage.currentPage, locationPage.pageCount, locationPage.start, locationPage.end, locations.length)}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
              {locationPage.items.map((location) => (
                <EntityFocusCard
                  key={location.id}
                  entity={location}
                  isHighlighted={highlightedItem?.id === location.id}
                  onFocus={handleLongPress}
                  icon={MapPin}
                  aspectRatio="16/10"
                />
              ))}
            </div>
          </div>
        );

      case 'storyboard':
        return (
          <div>
            {renderPagination('storyboard', 'Storyboard page', storyboardPage.currentPage, storyboardPage.pageCount, storyboardPage.start, storyboardPage.end, storyboardFrames.length)}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
              {storyboardPage.items.map((frame) => (
                <StoryboardFocusCard
                  key={frame.id}
                  frame={frame}
                  isHighlighted={highlightedItem?.id === frame.id}
                  onFocus={handleLongPress}
                />
              ))}
            </div>
          </div>
        );

      case 'script':
        return (
          <div
            style={{
              padding: '14px',
              background: 'var(--bg-secondary)',
              borderRadius: '10px',
              border: '1px solid var(--border-subtle)',
            }}
          >
            {scriptText ? (
              <pre
                style={{
                  margin: 0,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontFamily: 'IBM Plex Serif, serif',
                  fontSize: '13px',
                  lineHeight: 1.6,
                  color: 'var(--text-primary)',
                }}
              >
                {scriptText}
              </pre>
            ) : (
              <p style={{ fontSize: '13px', color: 'var(--text-secondary)', textAlign: 'center' }}>
                No script text available yet.
              </p>
            )}
          </div>
        );

      case 'props':
        return (
          <div>
            {renderPagination('props', 'Prop page', propPage.currentPage, propPage.pageCount, propPage.start, propPage.end, props.length)}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
              {propPage.items.map((prop) => (
                <EntityFocusCard
                  key={prop.id}
                  entity={prop}
                  isHighlighted={highlightedItem?.id === prop.id}
                  onFocus={handleLongPress}
                  icon={Package}
                  aspectRatio="1/1"
                />
              ))}
            </div>
          </div>
        );

      case 'video':
        return (
          <div
            style={{
              padding: '20px',
              background: 'var(--bg-secondary)',
              borderRadius: '10px',
              border: '1px solid var(--border-subtle)',
              textAlign: 'center',
              color: 'var(--text-secondary)',
            }}
          >
            Video review and playback will appear here once the timeline is approved and clips are generated.
          </div>
        );

      default:
        return (
          <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-secondary)' }}>
            <p>Content for {activeTabState} tab</p>
          </div>
        );
    }
  };

  return (
    <div
      data-testid="mobile-detail-view"
      style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      background: 'var(--bg-primary)',
    }}
    >
      {/* Header */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 16px',
        borderBottom: '1px solid var(--border-subtle)',
        background: 'var(--bg-secondary)',
      }}>
        <button 
          data-testid="mobile-detail-open-chat"
          onClick={() => setMobileView('chat')}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            background: 'none',
            border: 'none',
            color: 'var(--text-primary)',
            fontSize: '14px',
            padding: '8px',
          }}
        >
          <MessageSquare size={20} />
          <span>Chat</span>
        </button>
        
        <span style={{ fontSize: '16px', fontWeight: 600 }}>Details</span>
        
        <div style={{ width: '60px' }} />
      </div>

      {/* Tabs */}
      <div style={{
        display: 'flex',
        gap: '4px',
        padding: '12px 16px',
        borderBottom: '1px solid var(--border-subtle)',
        overflowX: 'auto',
        scrollbarWidth: 'none',
      }}>
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTabState === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTabState(tab.id)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '4px',
                padding: '8px 12px',
                background: isActive ? 'var(--accent)' : 'var(--bg-secondary)',
                border: 'none',
                borderRadius: '8px',
                color: isActive ? 'var(--bg-primary)' : 'var(--text-secondary)',
                fontSize: '12px',
                fontWeight: isActive ? 500 : 400,
                whiteSpace: 'nowrap',
              }}
            >
              <Icon size={14} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Content */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '16px',
      }}>
        {renderContent()}
      </div>

      {/* Hint */}
      <div style={{
        padding: '10px 16px',
        borderTop: '1px solid var(--border-subtle)',
        background: 'var(--bg-secondary)',
        textAlign: 'center',
      }}>
        <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
          Long press any item to focus the agent
        </p>
      </div>
    </div>
  );
}
