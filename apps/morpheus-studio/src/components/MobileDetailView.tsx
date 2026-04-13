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
} from 'lucide-react';
import type { TabType, Entity } from '../types';

const tabs: { id: TabType; label: string; icon: React.ElementType }[] = [
  { id: 'outline', label: 'Outline', icon: FileText },
  { id: 'script', label: 'Script', icon: Scroll },
  { id: 'cast', label: 'Cast', icon: Users },
  { id: 'locations', label: 'Locs', icon: MapPin },
  { id: 'props', label: 'Props', icon: Package },
  { id: 'storyboard', label: 'Board', icon: LayoutGrid },
  { id: 'video', label: 'Video', icon: Play },
];

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

  const cast = entities.filter((e): e is Entity & { type: 'cast' } => e.type === 'cast');
  const locations = entities.filter((e): e is Entity & { type: 'location' } => e.type === 'location');
  const props = entities.filter((e): e is Entity & { type: 'prop' } => e.type === 'prop');

  const handleLongPress = (item: { type: string; id: string; name: string }) => {
    injectFocusToChat(item);
    setMobileView('chat');
  };

  const renderContent = () => {
    switch (activeTabState) {
      case 'outline':
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {skeletonPlan ? (
              skeletonPlan.scenes.map((scene) => {
                const { handlers, isPressing } = useLongPress({
                  onLongPress: () => handleLongPress({ type: 'scene', id: scene.id, name: `Scene ${scene.number}` }),
                  ms: 600,
                });

                return (
                  <div
                    key={scene.id}
                    {...handlers}
                    style={{
                      padding: '14px',
                      background: 'var(--bg-secondary)',
                      borderRadius: '10px',
                      border: highlightedItem?.id === scene.id 
                        ? '2px solid var(--success)' 
                        : isPressing 
                          ? '2px solid var(--success)' 
                          : '1px solid var(--border-subtle)',
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
                    {isPressing && (
                      <div style={{
                        position: 'absolute',
                        inset: 0,
                        background: 'rgba(16, 185, 129, 0.15)',
                        borderRadius: '10px',
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
                    )}
                  </div>
                );
              })
            ) : (
              <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-secondary)' }}>
                <p>No outline generated yet.</p>
              </div>
            )}
          </div>
        );

      case 'cast':
        return (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
            {cast.map((member) => {
              const { handlers, isPressing } = useLongPress({
                onLongPress: () => handleLongPress({ type: 'entity', id: member.id, name: member.name }),
                ms: 600,
              });

              return (
                <div
                  key={member.id}
                  {...handlers}
                  style={{
                    position: 'relative',
                    borderRadius: '10px',
                    overflow: 'hidden',
                    border: highlightedItem?.id === member.id 
                      ? '2px solid var(--success)' 
                      : isPressing 
                        ? '2px solid var(--success)' 
                        : '1px solid var(--border-subtle)',
                  }}
                >
                  <div style={{ aspectRatio: '3/4' }}>
                    {member.imageUrl ? (
                      <img src={member.imageUrl} alt={member.name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                    ) : (
                      <div style={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        justifyContent: 'center',
                        height: '100%',
                        background: 'var(--bg-tertiary)'
                      }}>
                        <Users size={28} style={{ color: 'var(--text-muted)' }} />
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
                    <span style={{ fontSize: '13px', fontWeight: 500 }}>{member.name}</span>
                  </div>
                  {isPressing && (
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
                  )}
                </div>
              );
            })}
          </div>
        );

      case 'locations':
        return (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
            {locations.map((location) => {
              const { handlers, isPressing } = useLongPress({
                onLongPress: () => handleLongPress({ type: 'entity', id: location.id, name: location.name }),
                ms: 600,
              });

              return (
                <div
                  key={location.id}
                  {...handlers}
                  style={{
                    position: 'relative',
                    borderRadius: '10px',
                    overflow: 'hidden',
                    border: highlightedItem?.id === location.id 
                      ? '2px solid var(--success)' 
                      : isPressing 
                        ? '2px solid var(--success)' 
                        : '1px solid var(--border-subtle)',
                  }}
                >
                  <div style={{ aspectRatio: '16/10' }}>
                    {location.imageUrl ? (
                      <img src={location.imageUrl} alt={location.name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                    ) : (
                      <div style={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        justifyContent: 'center',
                        height: '100%',
                        background: 'var(--bg-tertiary)'
                      }}>
                        <MapPin size={28} style={{ color: 'var(--text-muted)' }} />
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
                    <span style={{ fontSize: '13px', fontWeight: 500 }}>{location.name}</span>
                  </div>
                  {isPressing && (
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
                  )}
                </div>
              );
            })}
          </div>
        );

      case 'storyboard':
        return (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
            {storyboardFrames.map((frame) => {
              const { handlers, isPressing } = useLongPress({
                onLongPress: () => handleLongPress({ type: 'storyboard', id: frame.id, name: `Storyboard ${frame.sequence}` }),
                ms: 600,
              });

              return (
                <div
                  key={frame.id}
                  {...handlers}
                  style={{
                    position: 'relative',
                    borderRadius: '10px',
                    overflow: 'hidden',
                    border: highlightedItem?.id === frame.id 
                      ? '2px solid var(--success)' 
                      : isPressing 
                        ? '2px solid var(--success)' 
                        : '1px solid var(--border-subtle)',
                  }}
                >
                  <div style={{ aspectRatio: '16/10' }}>
                    <img src={frame.imageUrl} alt={frame.description} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
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
                  {isPressing && (
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
                  )}
                </div>
              );
            })}
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
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '12px' }}>
            {props.map((prop) => {
              const { handlers, isPressing } = useLongPress({
                onLongPress: () => handleLongPress({ type: 'entity', id: prop.id, name: prop.name }),
                ms: 600,
              });

              return (
                <div
                  key={prop.id}
                  {...handlers}
                  style={{
                    position: 'relative',
                    borderRadius: '10px',
                    overflow: 'hidden',
                    border: highlightedItem?.id === prop.id 
                      ? '2px solid var(--success)' 
                      : isPressing 
                        ? '2px solid var(--success)' 
                        : '1px solid var(--border-subtle)',
                  }}
                >
                  <div style={{ aspectRatio: '1/1' }}>
                    {prop.imageUrl ? (
                      <img src={prop.imageUrl} alt={prop.name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                    ) : (
                      <div style={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        justifyContent: 'center',
                        height: '100%',
                        background: 'var(--bg-tertiary)'
                      }}>
                        <Package size={28} style={{ color: 'var(--text-muted)' }} />
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
                    <span style={{ fontSize: '13px', fontWeight: 500 }}>{prop.name}</span>
                  </div>
                  {isPressing && (
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
                  )}
                </div>
              );
            })}
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
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      background: 'var(--bg-primary)',
    }}>
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
