import React, { useState, useEffect, useCallback, useRef } from 'react';
import { ReactUI } from 'freesail';
import { StandardCatalog } from '@freesail/standard-catalog';
import { ChatCatalog } from '@freesail/chat-catalog';
import {WeatherCatalog} from "@freesail-community/weather-catalog";

const CHAT_CATALOG_ID = ChatCatalog.namespace;
const ALL_CATALOGS: ReactUI.CatalogDefinition[] = [StandardCatalog, ChatCatalog, WeatherCatalog];

function getGatewayUrl(): string {
  const gatewayUrl = import.meta.env['VITE_GATEWAY_URL'] as string | undefined;
  if (gatewayUrl) {
    if (gatewayUrl.startsWith('/')) {
      return `${window.location.protocol}//${window.location.host}${gatewayUrl.replace(/\/$/, '')}`;
    }
    return gatewayUrl.replace(/\/$/, '');
  }
  const port = import.meta.env['VITE_GATEWAY_PORT'] ?? '3001';
  return `${window.location.protocol}//${window.location.hostname}:${port}`;
}

// ============================================================================
// useWindowWidth — tracks viewport width for percentage-based constraints
// ============================================================================

function useWindowWidth() {
  const [width, setWidth] = useState(window.innerWidth);
  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return width;
}

// ============================================================================
// useDrag — reusable resize hook
// direction: 'right' = grows rightward (chat), 'left' = grows leftward (sidebar)
// ============================================================================

function useDrag(initialWidth: number, min: number, max: number, direction: 'right' | 'left') {
  const [width, setWidth] = useState(initialWidth);
  const isDragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(initialWidth);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    isDragging.current = true;
    startX.current = e.clientX;
    startWidth.current = width;
    e.preventDefault();
  }, [width]);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!isDragging.current) return;
      const delta = e.clientX - startX.current;
      const newWidth = direction === 'right'
        ? startWidth.current + delta
        : startWidth.current - delta;
      setWidth(Math.max(min, Math.min(max, newWidth)));
    };
    const onMouseUp = () => { isDragging.current = false; };
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, [min, max, direction]);

  // Clamp width into new bounds when window resizes
  useEffect(() => {
    setWidth(w => Math.max(min, Math.min(max, w)));
  }, [min, max]);

  return { width, onMouseDown, isDraggingRef: isDragging };
}

// ============================================================================
// App
// ============================================================================

function App() {
  const [themeMode, setThemeMode] = useState<'light' | 'dark'>('light');
  const windowWidth = useWindowWidth();

  const chatMin = Math.floor(windowWidth * 0.25);
  const chatMax = Math.floor(windowWidth * 0.5);
  const chat = useDrag(Math.floor(windowWidth * 0.4), chatMin, chatMax, 'right');

  return (
      <ReactUI.FreesailProvider
        theme={themeMode}
        catalogs={ALL_CATALOGS}
        onConnectionChange={(connected) => console.log('Connection status:', connected)}
        onError={(error) => console.error('Freesail error:', error)}
      >
        <ChatBootstrapper />
        <AppLayout
          themeMode={themeMode}
          setThemeMode={setThemeMode}
          chat={chat}
        />
      </ReactUI.FreesailProvider>
  );
}

// ============================================================================
// AppLayout — inside FreesailProvider so it can call useSurfaces()
// ============================================================================

function AppLayout({
  themeMode,
  setThemeMode,
  chat,
}: {
  themeMode: 'light' | 'dark';
  setThemeMode: (m: 'light' | 'dark') => void;
  chat: ReturnType<typeof useDrag>;
}) {
  return (
    <div style={{
      display: 'flex',
      height: '100vh',
      overflow: 'hidden',
      userSelect: chat.isDraggingRef.current ? 'none' : 'auto',
    }}>
      {/* Chat panel — fixed width on the left */}
      <div style={{
        width: `${chat.width}px`,
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        borderRight: '1px solid var(--freesail-border)',
      }}>
        <ChatPanelHeader themeMode={themeMode} setThemeMode={setThemeMode} />
        <div style={{ flex: 1, overflow: 'hidden', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <ReactUI.FreesailSurface surfaceId="__chat" />
        </div>
      </div>

      {/* Resize handle */}
      <ResizeHandle onMouseDown={chat.onMouseDown} />

      {/* Right panel — promo by default, surfaces when available */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', minWidth: 0 }}>
        <SurfaceSidebar />
      </div>
    </div>
  );
}

// ============================================================================
// ChatPanelHeader
// ============================================================================

function ChatPanelHeader({
  themeMode,
  setThemeMode,
}: {
  themeMode: 'light' | 'dark';
  setThemeMode: (m: 'light' | 'dark') => void;
}) {
  const { isConnected } = ReactUI.useConnectionStatus();

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '6px 10px',
      borderBottom: '1px solid var(--freesail-border)',
      flexShrink: 0,
      backgroundColor: 'var(--freesail-bg)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <div style={{
          width: '8px',
          height: '8px',
          borderRadius: '50%',
          backgroundColor: isConnected ? '#22c55e' : '#ef4444',
          flexShrink: 0,
        }} />
        <img src='/assets/favicon.ico' height='30px' alt='Freesail'/>
      </div>
      <div style={{ display: 'flex', gap: '2px' }}>
        {(['light', 'dark'] as const).map((mode) => (
          <button
            key={mode}
            onClick={() => setThemeMode(mode)}
            style={{
              padding: '3px 8px',
              fontSize: '11px',
              border: 'none',
              borderRadius: 'var(--freesail-radius-sm)',
              cursor: 'pointer',
              background: themeMode === mode
                ? 'var(--freesail-bg-muted, rgba(0,0,0,0.08))'
                : 'transparent',
              color: themeMode === mode
                ? 'var(--freesail-text-main)'
                : 'var(--freesail-text-muted)',
              fontWeight: themeMode === mode ? '500' : 'normal',
            }}
          >
            {mode === 'light' ? 'Light' : 'Dark'}
          </button>
        ))}
      </div>
    </div>
  );
}

// ============================================================================
// ResizeHandle
// ============================================================================

function ResizeHandle({ onMouseDown }: { onMouseDown: (e: React.MouseEvent) => void }) {
  return (
    <div
      onMouseDown={onMouseDown}
      style={{
        width: '3px',
        cursor: 'col-resize',
        background: 'var(--freesail-border)',
        flexShrink: 0,
        transition: 'background 0.15s',
      }}
      onMouseEnter={e => (e.currentTarget.style.background = 'var(--freesail-primary)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'var(--freesail-border)')}
    />
  );
}

// ============================================================================
// ChatBootstrapper
// ============================================================================

function ChatBootstrapper() {
  const { surfaceManager } = ReactUI.useFreesailContext();

  useEffect(() => {
    surfaceManager.createSurface({
      surfaceId: '__chat',
      catalogId: CHAT_CATALOG_ID,
      sendDataModel: false,
    });

    surfaceManager.updateComponents('__chat', [
      {
        id: 'root',
        component: 'ChatContainer',
        title: 'Chat',
        height: '100%',
        children: ['message_list', 'agent_stream', 'typing', 'chat_input'],
      },
      {
        id: 'message_list',
        component: 'ChatMessageList',
        children: { componentId: 'msg_template', path: '/messages' },
      },
      {
        id: 'msg_template',
        component: 'ChatMessage',
        role: { path: 'role' },
        content: { path: 'content' },
        timestamp: { path: 'timestamp' },
      },
      {
        id: 'agent_stream',
        component: 'AgentStream',
        token: { path: '/stream/token' },
        active: { path: '/stream/active' },
      },
      {
        id: 'typing',
        component: 'ChatTypingIndicator',
        visible: { path: '/isTyping' },
        text: 'Thinking...',
      },
      {
        id: 'chat_input',
        component: 'ChatInput',
        placeholder: 'Type a message...',
        sendIcon: 'send',
      },
    ]);

    surfaceManager.updateDataModel('__chat', '/', { messages: [], isTyping: false, stream: { token: '', active: false } });
  }, [surfaceManager]);

  return null;
}

// ============================================================================
// SurfaceSidebar
// ============================================================================

function labelFromSurfaceId(surfaceId: string): string {
  const stripped = surfaceId.replace(/^[_-]+|[_-]+$/g, '');
  const spaced = stripped.replace(/[_-]/g, ' ');
  return spaced.toUpperCase().replace('SURFACE','');
}

const ATTENTION_STYLES = `
  @keyframes fs-ping {
    0%   { transform: scale(1);   opacity: 0.8; }
    70%  { transform: scale(2.4); opacity: 0; }
    100% { transform: scale(2.4); opacity: 0; }
  }
  @keyframes fs-tab-glow {
    0%, 100% { box-shadow: 0 2px 0 0 var(--freesail-primary); opacity: 1; }
    50%       { box-shadow: 0 2px 8px 2px var(--freesail-primary); opacity: 0.7; }
  }
`;

// ============================================================================
// FreesailPromo — shown in the right panel before any agent surfaces appear
// ============================================================================

function FreesailPromo() {
  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: '28px',
      padding: '40px 32px',
      backgroundColor: 'var(--freesail-bg)',
      color: 'var(--freesail-text-main)',
      textAlign: 'center',
    }}>
      <img src='/assets/favicon.ico' height='56px' alt='Freesail' style={{ opacity: 0.9 }} />

      <div>
        <div style={{ fontSize: '22px', fontWeight: '700', marginBottom: '8px', letterSpacing: '-0.3px' }}>
          Freesail
        </div>
        <div style={{ fontSize: '14px', color: 'var(--freesail-text-muted)', lineHeight: '1.6', maxWidth: '320px' }}>
          Agent-driven UI — let your AI agent build and update this panel in real time as the conversation unfolds.
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', width: '100%', maxWidth: '300px' }}>
        {[
          { icon: '⚡', title: 'Live surfaces', desc: 'Agents push UI components directly into this panel.' },
          { icon: '🔌', title: 'Any stack', desc: 'Works with any LLM or agent framework via the gateway.' },
          { icon: '🧩', title: 'Extensible', desc: 'Bring your own component catalogs alongside the built-ins.' },
        ].map(({ icon, title, desc }) => (
          <div key={title} style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: '12px',
            padding: '12px 14px',
            borderRadius: 'var(--freesail-radius-md)',
            backgroundColor: 'var(--freesail-bg-muted, rgba(0,0,0,0.04))',
            textAlign: 'left',
          }}>
            <span style={{ fontSize: '18px', flexShrink: 0 }}>{icon}</span>
            <div>
              <div style={{ fontSize: '13px', fontWeight: '600', marginBottom: '2px' }}>{title}</div>
              <div style={{ fontSize: '12px', color: 'var(--freesail-text-muted)', lineHeight: '1.5' }}>{desc}</div>
            </div>
          </div>
        ))}
      </div>

      <div style={{ fontSize: '11px', color: 'var(--freesail-text-muted)' }}>
        Start chatting — agent surfaces will appear here.
      </div>
    </div>
  );
}

function SurfaceSidebar() {
  const allSurfaces = ReactUI.useSurfaces();
  const agentSurfaces = allSurfaces.filter(s => !s.id.startsWith('__'));
  const [activeId, setActiveId] = useState<string | null>(null);
  const [attentionIds, setAttentionIds] = useState<Set<string>>(new Set());
  const { surfaceManager } = ReactUI.useFreesailContext();
  const activeIdRef = useRef<string | null>(null);
  const hasActiveSurfaceRef = useRef<boolean>(false);

  useEffect(() => { activeIdRef.current = activeId; }, [activeId]);

  useEffect(() => {
    hasActiveSurfaceRef.current = activeId !== null && agentSurfaces.some(s => s.id === activeId);
  }, [activeId, agentSurfaces]);

  useEffect(() => {
    if (document.getElementById('fs-attention-styles')) return;
    const el = document.createElement('style');
    el.id = 'fs-attention-styles';
    el.textContent = ATTENTION_STYLES;
    document.head.appendChild(el);
  }, []);

  // Auto-switch if no active surface; otherwise highlight the tab
  useEffect(() => {
    const handler = (surfaceId: string) => {
      if (surfaceId.startsWith('__')) return;
      if (!hasActiveSurfaceRef.current) {
        setActiveId(surfaceId);
        setAttentionIds(prev => { const next = new Set(prev); next.delete(surfaceId); return next; });
      } else if (surfaceId !== activeIdRef.current) {
        setAttentionIds(prev => {
          if (prev.has(surfaceId)) return prev;
          const next = new Set(prev);
          next.add(surfaceId);
          return next;
        });
      }
    };
    const createdHandler = (surface: { id: string }) => handler(surface.id);
    const unsubComponents = surfaceManager.on('componentsUpdated', handler);
    const unsubData = surfaceManager.on('dataModelUpdated', handler);
    const unsubCreated = surfaceManager.on('surfaceCreated', createdHandler);
    return () => { unsubComponents(); unsubData(); unsubCreated(); };
  }, [surfaceManager]);

  // When active surface is deleted, clear activeId so next activity auto-switches
  useEffect(() => {
    if (activeId && !agentSurfaces.find(s => s.id === activeId)) {
      setActiveId(null);
    }
  }, [agentSurfaces, activeId]);

  const handleTabClick = (id: string) => {
    setActiveId(id);
    setAttentionIds(prev => {
      if (!prev.has(id)) return prev;
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  };

  if (agentSurfaces.length === 0) return <FreesailPromo />;

  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      backgroundColor: 'var(--freesail-bg)',
      overflow: 'hidden',
    }}>
        {/* Tab bar */}
        <div style={{
          display: 'flex',
          overflowX: 'auto',
          borderBottom: '1px solid var(--freesail-border)',
          flexShrink: 0,
          scrollbarWidth: 'none',
        }}>
          {agentSurfaces.map(surface => {
            const hasAttention = attentionIds.has(surface.id) && activeId !== surface.id;
            return (
            <button
              key={surface.id}
              onClick={() => handleTabClick(surface.id)}
              style={{
                position: 'relative',
                padding: '8px 16px',
                border: 'none',
                borderBottom: activeId === surface.id
                  ? '2px solid var(--freesail-primary)'
                  : '2px solid transparent',
                background: 'transparent',
                color: activeId === surface.id
                  ? 'var(--freesail-text-main)'
                  : hasAttention
                    ? 'var(--freesail-primary)'
                    : 'var(--freesail-text-muted)',
                cursor: 'pointer',
                fontSize: '13px',
                fontWeight: (activeId === surface.id || hasAttention) ? '600' : 'normal',
                whiteSpace: 'nowrap',
                flexShrink: 0,
                animation: hasAttention ? 'fs-tab-glow 1.2s ease-in-out infinite' : 'none',
              }}
            >
              {labelFromSurfaceId(surface.id)}
              {hasAttention && (
                <span style={{ position: 'relative', display: 'inline-block', width: 8, height: 8, marginLeft: 6, verticalAlign: 'middle' }}>
                  {/* Solid core dot */}
                  <span style={{
                    position: 'absolute',
                    inset: 0,
                    borderRadius: '50%',
                    backgroundColor: 'var(--freesail-primary)',
                  }} />
                  {/* Sonar ping ring */}
                  <span style={{
                    position: 'absolute',
                    inset: 0,
                    borderRadius: '50%',
                    backgroundColor: 'var(--freesail-primary)',
                    animation: 'fs-ping 1.2s ease-out infinite',
                  }} />
                </span>
              )}
            </button>
            );
          })}
        </div>

        {/* Surface panels */}
        {agentSurfaces.map(surface => (
          <div
            key={surface.id}
            style={{
              display: activeId === surface.id ? 'flex' : 'none',
              flex: 1,
              overflow: 'auto',
              minHeight: 0,
            }}
          >
            <ReactUI.FreesailSurface surfaceId={surface.id} />
          </div>
        ))}
    </div>
  );
}

export default App;
