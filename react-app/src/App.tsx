import React, { useState, useEffect, useCallback, useRef } from 'react';
import { ReactUI } from 'freesail';
import { StandardCatalog } from '@freesail/standard-catalog';
import { ChatCatalog } from '@freesail/chat-catalog';

const CHAT_CATALOG_ID = ChatCatalog.namespace;
const ALL_CATALOGS: ReactUI.CatalogDefinition[] = [StandardCatalog, ChatCatalog];

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
  const chat = useDrag(340, chatMin, chatMax, 'right');

  return (
    <ReactUI.FreesailThemeProvider theme={themeMode}>
      <ReactUI.FreesailProvider
        gateway={getGatewayUrl()}
        catalogDefinitions={ALL_CATALOGS}
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
    </ReactUI.FreesailThemeProvider>
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
  const allSurfaces = ReactUI.useSurfaces();
  const hasSidebar = allSurfaces.some(s => !s.id.startsWith('__'));

  return (
    <div style={{
      display: 'flex',
      height: '100vh',
      overflow: 'hidden',
      userSelect: chat.isDraggingRef.current ? 'none' : 'auto',
    }}>
      {/* Chat panel — full width when no sidebar, fixed width otherwise */}
      <div style={{
        ...(hasSidebar
          ? { width: `${chat.width}px`, flexShrink: 0 }
          : { flex: 1 }),
        display: 'flex',
        flexDirection: 'column',
        borderRight: hasSidebar ? '1px solid var(--freesail-border)' : 'none',
      }}>
        <ChatPanelHeader themeMode={themeMode} setThemeMode={setThemeMode} />
        <div style={{ flex: 1, overflow: 'hidden', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <ReactUI.FreesailSurface surfaceId="__chat" />
        </div>
      </div>

      {/* Chat drag handle — only when sidebar is visible */}
      {hasSidebar && <ResizeHandle onMouseDown={chat.onMouseDown} />}

      {/* Sidebar fills remaining space */}
      {hasSidebar && (
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', minWidth: 0 }}>
          <SurfaceSidebar />
        </div>
      )}
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
      backgroundColor: 'var(--freesail-bg-surface)',
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

function SurfaceSidebar() {
  const allSurfaces = ReactUI.useSurfaces();
  const agentSurfaces = allSurfaces.filter(s => !s.id.startsWith('__'));
  const [activeId, setActiveId] = useState<string | null>(null);
  const { surfaceManager } = ReactUI.useFreesailContext();

  // Switch to whichever agent surface was most recently updated
  useEffect(() => {
    const handler = (surfaceId: string) => {
      if (!surfaceId.startsWith('__')) setActiveId(surfaceId);
    };
    const unsubComponents = surfaceManager.on('componentsUpdated', handler);
    const unsubData = surfaceManager.on('dataModelUpdated', handler);
    return () => { unsubComponents(); unsubData(); };
  }, [surfaceManager]);

  // Fallback: if active surface was deleted, switch to first available
  useEffect(() => {
    if (agentSurfaces.length > 0 && (!activeId || !agentSurfaces.find(s => s.id === activeId))) {
      setActiveId(agentSurfaces[0].id);
    }
  }, [agentSurfaces, activeId]);

  if (agentSurfaces.length === 0) return null;

  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      backgroundColor: 'var(--freesail-bg-surface)',
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
          {agentSurfaces.map(surface => (
            <button
              key={surface.id}
              onClick={() => setActiveId(surface.id)}
              style={{
                padding: '8px 16px',
                border: 'none',
                borderBottom: activeId === surface.id
                  ? '2px solid var(--freesail-primary)'
                  : '2px solid transparent',
                background: 'transparent',
                color: activeId === surface.id
                  ? 'var(--freesail-text-main)'
                  : 'var(--freesail-text-muted)',
                cursor: 'pointer',
                fontSize: '13px',
                fontWeight: activeId === surface.id ? '500' : 'normal',
                whiteSpace: 'nowrap',
                flexShrink: 0,
              }}
            >
              {labelFromSurfaceId(surface.id)}
            </button>
          ))}
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
