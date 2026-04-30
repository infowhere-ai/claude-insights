# Screenshots — Claude Insights Dashboard

> Captured from a live instance at 1400×900 (desktop) and 390×844 (mobile).
> All screenshots show real data — no mocks.

---

## Dashboard & Status

### 01 — Full Dashboard (WORKING state)
**File**: `01-dashboard-overview.png`
**Shows**: The complete dashboard during an active Claude Code session. Status bar reads "WORKING — Read", indicating a file read tool call is in progress. All panels are visible: Reasoning, Session Context, Activity, Tokens, Commands, Renewal Window, and Git. The LIVE badge in the header confirms real-time monitoring is active.
**Use for**: Hero image, README banner, landing page above the fold.

---

### 08 — Status Bar (WORKING)
**File**: `08-status-bar-working.png`
**Shows**: The status bar at the top of the left panel showing "WORKING — mcp__plugin_playwright__browser_take_screenshot" with a green pulsing dot. Demonstrates the real-time tool tracking — the exact tool name is shown as it executes.
**Use for**: "What it monitors" section, feature highlights for live status tracking.

---

### 09 — Live Badge & Header
**File**: `09-live-badge-header.png`
**Shows**: The top navigation header with the "LIVE · CLAUDE-MONITOR" badge (green dot), the project selector dropdown (multi-project support), and the session timestamp. The "Monitor" back-link is also visible.
**Use for**: Header section of README, feature overview for multi-project support.

---

### 16 — History Badge (idle project)
**File**: `16-history-badge.png`
**Shows**: The dashboard switched to the `standarts` project, which is idle. The badge reads "HISTORY · STANDARTS" and the status shows "IDLE". Activity panel shows 6 sessions and no current session data. Demonstrates automatic LIVE/HISTORY mode switching based on whether Claude Code is running in the project.
**Use for**: Live vs History comparison, badge state documentation.

---

### 17 — Historical Session (browsing past work)
**File**: `17-history-session.png`
**Shows**: A historical session from 2026-04-27 loaded on the claude-monitor project (badge remains LIVE since Claude is running). The Session Context panel shows the session's fixed files (97k tok), variable reads (7k tok), and conversation (795 tok). Commands panel lists all tool calls from that session with durations. Demonstrates session navigation while a live session runs in parallel.
**Use for**: Session history feature, "browse past sessions" documentation.

---

## Reasoning

### 02 — Reasoning Panel (live)
**File**: `02-reasoning-live.png`
**Shows**: The Reasoning panel during an active thinking block. Claude's internal reasoning text is streamed live as it thinks. Shows word count and block number ("32 words · block 3/3"). The panel has a distinct pink/magenta heading to visually separate reasoning from output.
**Use for**: Extended Thinking feature highlight, reasoning visibility documentation.

---

### 12 — Reasoning History Modal
**File**: `12-reasoning-history-modal.png`
**Shows**: The Reasoning History modal opened via the "hist" button. Lists all 123 reasoning blocks from the session with total word count (18,347 words). Blocks are collapsed by default — expanded blocks show the full thinking text. Demonstrates how the dashboard accumulates and preserves all reasoning across an entire session.
**Use for**: Reasoning history feature, "what gets tracked" section.

---

## Session Context Panel

### 03 — Session Context (token breakdown)
**File**: `03-session-context.png`
**Shows**: The Session Context panel with a detailed token breakdown. The progress bar is split into three segments: FIXED (CLAUDE.md + rules loaded at session start, ~97k tokens), CHAT (conversation messages, ~795 tokens), and VARIABLE (Read/Bash/Grep results accumulated during the session, ~7k tokens). Each segment shows percentage and token count. The FIXED section lists all loaded files with individual token costs.
**Use for**: Context window transparency feature, token breakdown documentation.

---

## Activity & Statistics

### 04 — Activity Panel
**File**: `04-activity-panel.png`
**Shows**: The Activity panel on the right sidebar showing aggregate stats: 22 sessions total, 939k tokens in the last 7 days, 14k tokens this session, and "Bash" as the top tool (711 calls this week). Gives a high-level view of usage patterns across all sessions for the project.
**Use for**: Usage analytics feature, activity tracking documentation.

---

### 14 — Renewal Window
**File**: `14-renewal-window.png`
**Shows**: The 5-hour token renewal window progress bar. Shows "293k tokens · 2 sessions" consumed with "1h 43m remaining" until the next window resets (shown in yellow/orange indicating approaching limit). Helps track Claude's rolling usage against the 5-hour billing window.
**Use for**: Token window feature, usage limit tracking documentation.

---

## Model & Tokens

### 05 — Model & Tokens Panel
**File**: `05-model-tokens.png`
**Shows**: The Tokens panel showing the model (SONNET-4-6) with a progress bar at 80% of context used. Breaks down the current context into: Input tokens (102), Output tokens (14k), Cache read (4.5M — large value from cache hits), and Context window usage (14k). The percentage and colour-coded bar give an instant visual of context pressure.
**Use for**: Context pressure monitoring feature, model token tracking documentation.

---

## Commands Panel

### 06 — Commands Panel
**File**: `06-commands-panel.png`
**Shows**: The Commands panel listing recent tool calls in chronological order. Each entry shows the tool icon, tool name, file path (truncated), execution duration (0.1s–0.3s), and a checkmark (success) or X (failure). Tools shown include Edit, Read, Bash, and Write — a typical development session mix.
**Use for**: Tool call tracking feature, command history documentation.

---

## Git Panel

### 07 — Git / TO COMMIT Panel
**File**: `07-git-panel.png`
**Shows**: The "TO COMMIT" panel listing uncommitted files in the project. Shows file status (M = modified, ? = untracked) and paths. Provides quick visibility into what Claude Code has changed without switching to a terminal.
**Use for**: Git integration feature, uncommitted changes tracking documentation.

---

## Multi-Project

### 10 — Project Selector
**File**: `10-project-dropdown.png`
**Shows**: Full dashboard view with the project selector visible in the header. The selector lists all monitored projects (claude-monitor, copilot-meeting, infowhere-ai-podcast, mcp-loki-log, project-cv, project-finances, project-ops-dashboard, standarts, standarts-llm). Demonstrates automatic multi-project discovery — any directory with a `.claude/` folder is detected.
**Use for**: Multi-project support feature, auto-discovery documentation.

---

## About

### 11 — About Dialog
**File**: `11-about-dialog.png`
**Shows**: The About modal with the Claude Insights logo, version (1.0.0-beta), and author attribution. Accessible via the info icon (?) in the top-right corner.
**Use for**: About section, credits, version documentation.

---

## Mobile

### 13 — Mobile View (390×844)
**File**: `13-mobile-view.png`
**Shows**: The dashboard on a 390×844 mobile viewport. The layout collapses to a single column — the right sidebar panels stack below the main content. The status bar and header wrap gracefully. All panels remain functional and readable on small screens.
**Use for**: Mobile responsiveness feature, responsive design documentation.

---

## Usage Notes for monitor.infowhere.ai

For the landing page, suggested layout:

1. **Hero**: `01-dashboard-overview.png` — full dashboard, WORKING state
2. **Feature: Live Monitoring**: `08-status-bar-working.png` + `09-live-badge-header.png`
3. **Feature: Reasoning Visibility**: `02-reasoning-live.png` + `12-reasoning-history-modal.png`
4. **Feature: Context Transparency**: `03-session-context.png` + `05-model-tokens.png`
5. **Feature: Activity & History**: `04-activity-panel.png` + `17-history-session.png`
6. **Feature: Session States**: `16-history-badge.png` (HISTORY) vs `09-live-badge-header.png` (LIVE)
7. **Feature: Multi-Project**: `10-project-dropdown.png`
8. **Feature: Token Window**: `14-renewal-window.png`
9. **Feature: Git Integration**: `07-git-panel.png`
10. **Feature: Mobile**: `13-mobile-view.png`
