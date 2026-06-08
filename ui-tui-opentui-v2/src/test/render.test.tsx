/**
 * Phase 1 render test (spec v4 §5 Layer 2). Mounts the App headlessly with a
 * store seeded by the scripted hello stream, asserts the captured frame is
 * THEMED (brand name/icon from the theme, not hardcoded), and that applying a
 * custom skin re-themes the brand name reactively.
 */
import { describe, expect, test } from 'bun:test'

import { createSessionStore } from '../logic/store.ts'
import { App } from '../view/App.tsx'
import { ThemeProvider } from '../view/theme.tsx'
import { captureFrame } from './lib/render.ts'

function seedHello(store: ReturnType<typeof createSessionStore>) {
  store.apply({ type: 'gateway.ready' })
  store.apply({ type: 'message.start' })
  store.apply({ type: 'message.delta', payload: { text: 'Hi there, glitch!' } })
  store.apply({ type: 'message.complete' })
}

describe('App render (Phase 1, themed)', () => {
  test('renders the streamed hello + default brand into the frame', async () => {
    const store = createSessionStore()
    seedHello(store)

    const frame = await captureFrame(
      () => (
        <ThemeProvider theme={() => store.state.theme}>
          <App store={store} />
        </ThemeProvider>
      ),
      { until: 'ready', width: 60, height: 16 }
    )

    expect(frame).toContain('Hermes Agent') // default brand.name
    expect(frame).toContain('ready')
    expect(frame).toContain('Type your message') // composer placeholder (brand.welcome)
    // Assistant text renders through the native markdown renderable (<code filetype="markdown">,
    // drawUnstyledText:false → smooth live, but tree-sitter doesn't settle in the headless test
    // renderer; markdown paint is verified in the live smoke). Assert the data reached the store:
    const parts = store.state.messages.at(-1)?.parts ?? []
    expect(parts.some(p => p.type === 'text' && p.text === 'Hi there, glitch!')).toBe(true)
  })

  test('applying a skin re-themes the brand name (skinnable, no hardcoding)', async () => {
    const store = createSessionStore()
    store.apply({ type: 'gateway.ready', payload: { skin: { branding: { agent_name: 'Zephyr' } } } })
    seedHello(store)

    const frame = await captureFrame(
      () => (
        <ThemeProvider theme={() => store.state.theme}>
          <App store={store} />
        </ThemeProvider>
      ),
      { width: 60, height: 16 }
    )

    expect(frame).toContain('Zephyr')
    expect(frame).not.toContain('Hermes Agent')
  })

  test('renders an inline tool part between text (ordered parts §7)', async () => {
    const store = createSessionStore()
    store.apply({ type: 'gateway.ready' })
    store.apply({ type: 'message.start' })
    store.apply({ type: 'message.delta', payload: { text: 'Listing files:' } })
    store.apply({ type: 'tool.start', payload: { tool_id: 't1', name: 'terminal' } })
    store.apply({
      type: 'tool.complete',
      payload: { tool_id: 't1', result_text: '{"output":"alpha.txt\\nbeta.txt","exit_code":0}' }
    })
    store.apply({ type: 'message.complete' })

    const frame = await captureFrame(
      () => (
        <ThemeProvider theme={() => store.state.theme}>
          <App store={store} />
        </ThemeProvider>
      ),
      { until: 'terminal', width: 60, height: 16 }
    )

    expect(frame).toContain('terminal') // tool name (inline, between text blocks)
    expect(frame).toContain('alpha.txt') // envelope-stripped output, block-rendered
    expect(frame).not.toContain('exit_code') // the {output,exit_code} envelope is stripped
    // the 'Listing files:' text part is markdown (live-rendered); assert it in the store:
    const parts = store.state.messages.at(-1)?.parts ?? []
    expect(parts.some(p => p.type === 'text' && p.text === 'Listing files:')).toBe(true)
  })

  test('an approval prompt replaces the composer (blocked) and renders the options', async () => {
    const store = createSessionStore()
    store.apply({ type: 'gateway.ready' })
    store.apply({ type: 'approval.request', payload: { command: 'rm -rf /tmp/x', description: 'Delete temp dir' } })

    const frame = await captureFrame(
      () => (
        <ThemeProvider theme={() => store.state.theme}>
          <App store={store} />
        </ThemeProvider>
      ),
      { until: 'Approval required', width: 72, height: 24 }
    )

    expect(frame).toContain('Approval required')
    expect(frame).toContain('rm -rf /tmp/x') // the command under review
    expect(frame).toContain('Approve once') // native <select> option
    expect(frame).toContain('Deny')
    expect(frame).not.toContain('Type your message') // composer is hidden while blocked
  })

  test('the pager overlay renders title + content and replaces the transcript/composer', async () => {
    const store = createSessionStore()
    store.apply({ type: 'gateway.ready' })
    store.pushUser('a previous message')
    store.openPager('Status', 'status line one\nstatus line two\nstatus line three')

    const frame = await captureFrame(
      () => (
        <ThemeProvider theme={() => store.state.theme}>
          <App store={store} />
        </ThemeProvider>
      ),
      { until: 'Status', width: 72, height: 18 }
    )

    expect(frame).toContain('Status') // pager title
    expect(frame).toContain('status line one') // paged content
    expect(frame).toContain('Esc/q close') // pager footer hint
    expect(frame).not.toContain('a previous message') // transcript replaced by the pager
    expect(frame).not.toContain('Type your message') // composer hidden while the pager is open
  })

  test('the session switcher renders session rows and replaces the composer', async () => {
    const store = createSessionStore()
    store.apply({ type: 'gateway.ready' })
    store.openSwitcher([
      { id: 's1', title: 'First chat', preview: 'hi', messageCount: 5 },
      { id: 's2', title: 'Second chat', preview: 'yo', messageCount: 12 }
    ])

    const frame = await captureFrame(
      () => (
        <ThemeProvider theme={() => store.state.theme}>
          <App store={store} />
        </ThemeProvider>
      ),
      { until: 'Resume a session', width: 72, height: 18 }
    )

    expect(frame).toContain('Resume a session') // switcher header
    expect(frame).toContain('First chat') // session row
    expect(frame).toContain('Second chat')
    expect(frame).not.toContain('Type your message') // composer hidden while switcher open
  })

  test('the composer shows a live slash-completions dropdown', async () => {
    const store = createSessionStore()
    store.apply({ type: 'gateway.ready' })
    store.setCompletions([
      { display: '/compact', meta: 'compress context', text: '/compact' },
      { display: '/clear', meta: '', text: '/clear' }
    ])

    const frame = await captureFrame(
      () => (
        <ThemeProvider theme={() => store.state.theme}>
          <App store={store} />
        </ThemeProvider>
      ),
      { until: '/compact', width: 72, height: 18 }
    )

    expect(frame).toContain('/compact') // candidate
    expect(frame).toContain('compress context') // its meta
    expect(frame).toContain('Tab complete') // dropdown hint
  })

  test('the status bar renders model · context% · cwd (item 14)', async () => {
    const store = createSessionStore()
    store.apply({ type: 'gateway.ready' })
    store.apply({
      type: 'session.info',
      payload: {
        model: 'anthropic/claude-opus-4-8',
        cwd: '/tmp/proj',
        branch: 'main',
        usage: { context_percent: 42 }
      }
    })

    const frame = await captureFrame(
      () => (
        <ThemeProvider theme={() => store.state.theme}>
          <App store={store} />
        </ThemeProvider>
      ),
      { until: 'claude-opus', width: 72, height: 18 }
    )

    expect(frame).toContain('claude-opus-4-8') // model (provider prefix trimmed)
    expect(frame).toContain('42%') // context usage percent
    expect(frame).toContain('/tmp/proj') // cwd
    expect(frame).toContain('main') // branch
  })

  test('the agents dashboard renders the subagent tree and replaces the transcript', async () => {
    const store = createSessionStore()
    store.apply({ type: 'gateway.ready' })
    store.pushUser('parent turn')
    store.apply({
      type: 'subagent.start',
      payload: { subagent_id: 'a1', goal: 'research the topic', model: 'haiku', depth: 0 }
    })
    store.apply({ type: 'subagent.tool', payload: { subagent_id: 'a1', tool_name: 'web_search', text: 'opentui' } })
    store.openDashboard()

    const frame = await captureFrame(
      () => (
        <ThemeProvider theme={() => store.state.theme}>
          <App store={store} />
        </ThemeProvider>
      ),
      { until: 'Agents', width: 72, height: 24 }
    )

    expect(frame).toContain('Agents') // dashboard header
    expect(frame).toContain('research the topic') // subagent goal (list + detail header)
    expect(frame).toContain('web_search') // last tool + live trace line (item 15)
    expect(frame).toContain('select') // footer hint "↑↓ select"
    expect(frame).not.toContain('parent turn') // transcript replaced by the dashboard
  })
})
