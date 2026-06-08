/**
 * AgentsDashboard — the delegation/subagents view (spec §2b; Ink `agentsOverlay`,
 * item 15 "look into an agent trace live"). Master-detail:
 *   - top: the subagents tracked from the `subagent.*` stream, indented by depth;
 *     ↑/↓ SELECT a row (highlighted).
 *   - bottom: the SELECTED subagent's live trace (goal · status · model, latest
 *     thought, and the tool/progress/summary log) — sticky-bottom so it follows
 *     live; PgUp/PgDn scroll it.
 * Esc/q close. §8 #2 scrollbox gotchas (minHeight:0, sticky bottom).
 */
import { type ScrollBoxRenderable } from '@opentui/core'
import { useKeyboard } from '@opentui/solid'
import { createSignal, For, Show } from 'solid-js'

import type { SubagentInfo } from '../../logic/store.ts'
import { useTheme } from '../theme.tsx'

const PAGE = 8

function statusColor(status: string, theme: ReturnType<typeof useTheme>): string {
  const c = theme().color
  if (status === 'complete') return c.ok
  if (status === 'tool' || status === 'working') return c.accent
  if (status.includes('error') || status === 'failed') return c.error
  return c.warn
}

export function AgentsDashboard(props: { subagents: SubagentInfo[]; onClose: () => void }) {
  const theme = useTheme()
  const [sel, setSel] = createSignal(0)
  let traceBox: ScrollBoxRenderable | undefined

  const count = () => props.subagents.length
  const selected = () => Math.min(sel(), Math.max(0, count() - 1))
  const current = () => props.subagents[selected()]

  useKeyboard(key => {
    if (key.name === 'escape' || key.name === 'q' || (key.ctrl && key.name === 'c')) {
      props.onClose()
      return
    }
    if (key.name === 'up') setSel(s => Math.max(0, s - 1))
    else if (key.name === 'down') setSel(s => Math.min(Math.max(0, count() - 1), s + 1))
    else if (key.name === 'pageup') traceBox?.scrollBy(-PAGE)
    else if (key.name === 'pagedown') traceBox?.scrollBy(PAGE)
  })

  return (
    <box style={{ borderColor: theme().color.accent, flexDirection: 'column', flexGrow: 1, minHeight: 0 }} border>
      <box style={{ flexShrink: 0, paddingLeft: 1 }}>
        <text fg={theme().color.accent}>
          <b>
            ⛓ Agents · {count()} subagent{count() === 1 ? '' : 's'}
          </b>
        </text>
      </box>

      {/* master: the subagent list (↑/↓ select) */}
      <box style={{ flexShrink: 0, flexDirection: 'column', maxHeight: 10 }}>
        <Show
          when={count() > 0}
          fallback={<text fg={theme().color.muted}>No subagents yet — delegate a task to spawn one.</text>}
        >
          <For each={props.subagents}>
            {(sa, i) => (
              <text onMouseDown={() => setSel(i())}>
                <span style={{ fg: theme().color.muted }}>{'  '.repeat(Math.max(0, sa.depth))}</span>
                <span style={{ fg: i() === selected() ? theme().color.accent : theme().color.muted }}>
                  {i() === selected() ? '▸ ' : '  '}
                </span>
                <span style={{ fg: statusColor(sa.status, theme) }}>{`● ${sa.status}`}</span>
                <span style={{ fg: theme().color.label }}>{`  ${sa.goal || sa.id}`}</span>
                <span style={{ fg: theme().color.muted }}>{sa.lastTool ? `  ⚡${sa.lastTool}` : ''}</span>
              </text>
            )}
          </For>
        </Show>
      </box>

      {/* detail: the selected subagent's live trace */}
      <box style={{ flexGrow: 1, minHeight: 0, flexDirection: 'column', borderColor: theme().color.border }} border>
        <Show when={current()} fallback={<text fg={theme().color.muted}> </text>}>
          {sa => (
            <>
              <box style={{ flexShrink: 0, paddingLeft: 1 }}>
                <text>
                  <span style={{ fg: theme().color.label }}>{sa().goal || sa().id}</span>
                  <span style={{ fg: statusColor(sa().status, theme) }}>{`  · ${sa().status}`}</span>
                  <span style={{ fg: theme().color.muted }}>{sa().model ? `  · ${sa().model}` : ''}</span>
                </text>
              </box>
              <Show when={sa().thought}>
                <box style={{ flexShrink: 0, paddingLeft: 1 }}>
                  <text>
                    <span style={{ fg: theme().color.muted }}>{`🧠 ${sa().thought}`}</span>
                  </text>
                </box>
              </Show>
              <box style={{ flexGrow: 1, minHeight: 0, paddingLeft: 1 }}>
                <scrollbox ref={el => (traceBox = el)} style={{ flexGrow: 1, minHeight: 0 }} stickyScroll stickyStart="bottom">
                  <Show
                    when={(sa().trace?.length ?? 0) > 0}
                    fallback={<text fg={theme().color.muted}>(no activity yet)</text>}
                  >
                    <For each={sa().trace ?? []}>
                      {line => (
                        <text>
                          <span style={{ fg: theme().color.muted }}>{line}</span>
                        </text>
                      )}
                    </For>
                  </Show>
                </scrollbox>
              </box>
            </>
          )}
        </Show>
      </box>

      <box style={{ flexShrink: 0, paddingLeft: 1 }}>
        <text fg={theme().color.muted}>Esc/q close · ↑↓ select · PgUp/PgDn scroll trace</text>
      </box>
    </box>
  )
}
