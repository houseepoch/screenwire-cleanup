import { useMorpheusStore } from '../store';
import { 
  FileText, 
  Scroll, 
  Users, 
  MapPin, 
  Package, 
  LayoutGrid, 
  Play,
  Check,
  Clock,
  AlertCircle
} from 'lucide-react';
import type { TabType, Entity } from '../types';

const tabs: { id: TabType; label: string; icon: React.ElementType }[] = [
  { id: 'outline', label: 'Outline', icon: FileText },
  { id: 'script', label: 'Script', icon: Scroll },
  { id: 'cast', label: 'Cast', icon: Users },
  { id: 'locations', label: 'Locations', icon: MapPin },
  { id: 'props', label: 'Props', icon: Package },
  { id: 'storyboard', label: 'Storyboard', icon: LayoutGrid },
  { id: 'video', label: 'Video', icon: Play },
];

export function LeftPanel() {
  const { 
    activeTab, 
    setActiveTab, 
    entities, 
    storyboardFrames,
    currentProject,
    skeletonPlan,
  } = useMorpheusStore();

  const cast = entities.filter((e): e is Entity & { type: 'cast' } => e.type === 'cast');
  const locations = entities.filter((e): e is Entity & { type: 'location' } => e.type === 'location');
  const props = entities.filter((e): e is Entity & { type: 'prop' } => e.type === 'prop');

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

  const renderContent = () => {
    switch (activeTab) {
      case 'outline':
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div className="skeleton-viewer">
              {skeletonPlan ? (
                skeletonPlan.scenes.map((scene) => (
                  <div key={scene.id} className="skeleton-scene">
                    <div className="skeleton-scene-header">
                      <span className="skeleton-scene-number">Scene {scene.number}</span>
                      <span className="skeleton-scene-location">{scene.location}</span>
                    </div>
                    <p className="skeleton-scene-description">{scene.description}</p>
                  </div>
                ))
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
              background: 'var(--bg-primary)', 
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
                {currentProject?.name || 'UNTITLED'}
              </p>
              
              <div style={{ marginBottom: '24px' }}>
                <p style={{ 
                  textTransform: 'uppercase',
                  fontSize: '13px',
                  color: 'var(--text-muted)',
                  marginBottom: '8px'
                }}>
                  FADE IN:
                </p>
                <p style={{ 
                  textTransform: 'uppercase',
                  fontSize: '13px',
                  color: 'var(--text-muted)'
                }}>
                  EXT. COASTAL ROAD - DUSK
                </p>
              </div>

              <div style={{ marginBottom: '16px' }}>
                <p style={{ marginBottom: '12px' }}>
                  A vintage sedan winds along the cliffside road. The lighthouse looms in the distance, 
                  its beam cutting through the gathering fog.
                </p>
              </div>

              <div style={{ marginBottom: '16px' }}>
                <p style={{ textTransform: 'uppercase', fontSize: '13px', marginBottom: '8px' }}>
                  Elena (V.O.)
                </p>
                <p style={{ marginLeft: '24px' }}>
                  They said he hasn't spoken to anyone in fifteen years.
                </p>
              </div>

              <div style={{ marginBottom: '16px' }}>
                <p style={{ 
                  textTransform: 'uppercase',
                  fontSize: '13px',
                  color: 'var(--text-muted)'
                }}>
                  INT. CAR - CONTINUOUS
                </p>
              </div>

              <div>
                <p>
                  ELENA VOSS (30s), determined eyes fixed on the road ahead, grips the steering wheel.
                  Her phone buzzes. She ignores it.
                </p>
              </div>
            </div>
          </div>
        );

      case 'cast':
        return (
          <div className="entity-grid">
            {cast.map((member) => (
              <div key={member.id} className="entity-card">
                <div className="entity-card-image">
                  {member.thumbnailUrl || member.imageUrl ? (
                    <img src={member.thumbnailUrl || member.imageUrl} alt={member.name} loading="lazy" />
                  ) : (
                    <div style={{ 
                      display: 'flex', 
                      alignItems: 'center', 
                      justifyContent: 'center',
                      height: '100%',
                      background: 'var(--bg-tertiary)'
                    }}>
                      <Users size={32} style={{ color: 'var(--text-muted)' }} />
                    </div>
                  )}
                </div>
                <div className="entity-card-info">
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
                    <span className="entity-card-name">{member.name}</span>
                    {getStatusIcon(member.status)}
                  </div>
                  <span className="entity-card-type">Cast</span>
                  <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '6px', lineHeight: 1.4 }}>
                    {member.description}
                  </p>
                </div>
              </div>
            ))}
            <div 
              className="entity-card"
              style={{ 
                display: 'flex', 
                alignItems: 'center', 
                justifyContent: 'center',
                minHeight: '200px',
                border: '2px dashed var(--border-subtle)',
                background: 'transparent'
              }}
            >
              <div style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
                <Users size={24} style={{ margin: '0 auto 8px' }} />
                <span style={{ fontSize: '13px' }}>Add cast member</span>
              </div>
            </div>
          </div>
        );

      case 'locations':
        return (
          <div className="entity-grid">
            {locations.map((location) => (
              <div key={location.id} className="entity-card">
                <div className="entity-card-image" style={{ aspectRatio: '16/9' }}>
                  {location.thumbnailUrl || location.imageUrl ? (
                    <img src={location.thumbnailUrl || location.imageUrl} alt={location.name} loading="lazy" />
                  ) : (
                    <div style={{ 
                      display: 'flex', 
                      alignItems: 'center', 
                      justifyContent: 'center',
                      height: '100%',
                      background: 'var(--bg-tertiary)'
                    }}>
                      <MapPin size={32} style={{ color: 'var(--text-muted)' }} />
                    </div>
                  )}
                </div>
                <div className="entity-card-info">
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
                    <span className="entity-card-name">{location.name}</span>
                    {getStatusIcon(location.status)}
                  </div>
                  <span className="entity-card-type">Location</span>
                  <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '6px', lineHeight: 1.4 }}>
                    {location.description}
                  </p>
                </div>
              </div>
            ))}
          </div>
        );

      case 'props':
        return (
          <div className="entity-grid">
            {props.length > 0 ? (
              props.map((prop) => (
                <div key={prop.id} className="entity-card">
                  <div className="entity-card-image">
                    {prop.thumbnailUrl || prop.imageUrl ? (
                      <img src={prop.thumbnailUrl || prop.imageUrl} alt={prop.name} loading="lazy" />
                    ) : (
                      <div style={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        justifyContent: 'center',
                        height: '100%',
                        background: 'var(--bg-tertiary)'
                      }}>
                        <Package size={32} style={{ color: 'var(--text-muted)' }} />
                      </div>
                    )}
                  </div>
                  <div className="entity-card-info">
                    <span className="entity-card-name">{prop.name}</span>
                    <span className="entity-card-type">Prop</span>
                  </div>
                </div>
              ))
            ) : (
              <div style={{ 
                gridColumn: '1 / -1',
                textAlign: 'center', 
                padding: '40px 20px', 
                color: 'var(--text-secondary)',
                background: 'var(--bg-primary)',
                borderRadius: '12px',
                border: '1px dashed var(--border-subtle)'
              }}>
                <Package size={32} style={{ margin: '0 auto 12px', opacity: 0.5 }} />
                <p>No props generated yet.</p>
                <p style={{ fontSize: '13px', marginTop: '8px' }}>
                  Props will be identified during storyboard generation.
                </p>
              </div>
            )}
          </div>
        );

      case 'storyboard':
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div className="storyboard-grid" style={{ gridTemplateColumns: 'repeat(2, 1fr)' }}>
              {storyboardFrames.map((frame) => (
                <div key={frame.id} className="storyboard-panel">
                  {frame.thumbnailUrl || frame.imageUrl ? (
                    <img src={frame.thumbnailUrl || frame.imageUrl} alt={frame.description} loading="lazy" />
                  ) : (
                    <div style={{ 
                      display: 'flex', 
                      alignItems: 'center', 
                      justifyContent: 'center',
                      height: '100%',
                      background: 'var(--bg-tertiary)'
                    }}>
                      <LayoutGrid size={32} style={{ color: 'var(--text-muted)' }} />
                    </div>
                  )}
                  <div className="storyboard-panel-caption">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <span>{frame.shotType}:</span>
                      <span style={{ color: 'var(--text-secondary)' }}>{frame.description}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        );

      case 'video':
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <div className="video-player">
              <div style={{ 
                display: 'flex', 
                alignItems: 'center', 
                justifyContent: 'center',
                height: '100%',
                background: 'var(--bg-tertiary)',
                flexDirection: 'column',
                gap: '16px'
              }}>
                <Play size={48} style={{ color: 'var(--text-muted)' }} />
                <p style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>
                  Video preview will appear here after generation
                </p>
              </div>
            </div>
            <div style={{ display: 'flex', gap: '12px' }}>
              <button className="btn-accent" style={{ flex: 1 }}>
                <Play size={16} style={{ marginRight: '8px' }} />
                Play Preview
              </button>
              <button className="btn-secondary">
                Export
              </button>
            </div>
          </div>
        );

      default:
        return null;
    }
  };

  return (
    <div className="left-panel">
      <div className="panel-header">
        <h3 className="panel-title">{currentProject?.name}</h3>
        <p className="panel-subtitle">
          {currentProject?.status.replace(/_/g, ' ')}
        </p>
      </div>
      
      <div className="tabs-container">
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
      
      <div className="panel-content">
        {renderContent()}
      </div>
    </div>
  );
}
