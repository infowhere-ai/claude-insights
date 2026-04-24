# Claude Insights — Design Spec

> Data: 2026-04-24
> Estado: aprovado pelo utilizador
> Projecto: claude-monitor

---

## Visão Geral

Adicionar uma página `/insights` ao claude-monitor — uma página dedicada a explorar e compreender o comportamento do Claude: raciocínio interno, ferramentas usadas, métricas históricas e replay de sessões passadas.

A página é separada do monitor actual (que continua sem alterações) e serve como ferramenta de aprendizagem e observabilidade sobre o Claude.

---

## Decisões de Design

| Dimensão | Decisão |
|---|---|
| Estrutura | Página nova autónoma (`insights.html`) — não modifica `index.html` |
| Rota | `/insights` — nova rota FastAPI que serve `static/insights.html` |
| Layout | Dashboard: coluna esquerda (live 56%) + coluna direita (histórico) |
| Raciocínio (thinking) | Painel expansível — primeiras linhas visíveis, click para ver completo |
| Ferramentas | Feed de actividade cronológico com ícone, ficheiro/comando, duração e resultado |
| Stats | Grid 2×2 com métricas da semana (sessões, tokens, tool mais usada, cache hit %) |
| Sessões | Lista cronológica de sessões recentes; clicar carrega os dados históricos no mesmo layout |
| Git changes | Secção secundária no fundo da coluna direita; clicar num ficheiro abre modal de diff |
| Modal de diff | Overlay com syntax highlighting, navegação ← → entre ficheiros; reutiliza `/api/diff` |
| Navegação | Link "← Monitor" no topo para voltar; selector de projecto para filtrar |

---

## Arquitectura

### Ficheiros novos

```
claude-monitor/
├── static/
│   └── insights.html          # página completa (HTML/CSS/JS num só ficheiro)
└── app.py                     # adicionar rota GET /insights + novos endpoints
```

### Endpoints existentes reutilizados sem alteração

- `GET /api/diff?project=&file=` — diff de um ficheiro (para o modal)
- `GET /api/pending-files?project=` — ficheiros para commit (reimplementado em JS no insights.html — sem componente partilhado, `insights.html` é autónomo)

### SSE — extensão necessária

O stream `GET /events` existe e já emite eventos de `tool`, `status` e `stats`. É necessário estendê-lo para emitir também eventos de `thinking` sempre que um novo thinking block é detectado no JSONL activo. O backend já lê os JSONLs via `jsonl_watcher_loop` — basta adicionar detecção de novos thinking blocks ao loop e emiti-los via broadcast.

Formato do novo evento SSE:
```json
{ "type": "thinking", "project": "claude-monitor", "block_id": "uuid-ou-int-monotónico", "text": "...", "word_count": 183, "timestamp": "ISO 8601" }
```

O campo `block_id` distingue blocos de thinking: quando o `block_id` muda face ao último evento recebido, o frontend faz **append** (novo bloco); quando é igual, faz **replace** (actualização incremental do bloco em curso).

### Endpoints novos a criar

#### `GET /api/sessions`

Lista as sessões JSONL disponíveis para um projecto.

```
Query params:
  project: str — nome do projecto

Response: [
  {
    "session_id": "94615c4b-a969-4c00-af13-f52f4b694678",  // UUID = nome do dir JSONL; ordenado newest-first
    "started_at": "ISO 8601",
    "ended_at": "ISO 8601",   // timestamp do último evento; null se sessão activa
    "is_active": true
  }
]
```

O `session_id` é o nome do directório em `~/.claude/projects/<encoded-path>/` — é um UUID gerado pelo Claude Code. Não é necessário gerar identificadores separados.

O `started_at` é extraído do `timestamp` do primeiro evento no JSONL. O `ended_at` é o `timestamp` do último evento (null se `is_active: true`).

**Estado sem sessão activa:** quando nenhum projecto tem sessão activa (Claude não está a correr), a coluna esquerda mostra um placeholder: "Nenhuma sessão activa — abre o Claude Code para começar." O selector de projecto continua funcional e o utilizador pode navegar para sessões históricas.

#### `GET /api/session-detail`

Retorna thinking blocks, tool events e stats de uma sessão específica (activa ou histórica). **Este é o único endpoint de leitura de sessão** — usado tanto para o carregamento inicial da coluna live como para o replay de sessões passadas.

```
Query params:
  project: str      — nome do projecto
  session_id: str   — UUID do directório JSONL (de /api/sessions)

Response: {
  "thinking": [
    { "text": "...", "timestamp": "ISO 8601", "word_count": 183 }
  ],
  "tools": [
    { "tool": "Read", "input": "app.py", "duration_ms": 1200, "success": true, "timestamp": "ISO 8601" }
    // "input" é sempre uma string de resumo (ex: caminho do ficheiro para Read/Edit/Write,
    // primeiros 80 chars do comando para Bash). Nunca um objecto estruturado — simplifica o feed.
  ],
  "stats": {
    "input_tokens": 45000,
    "output_tokens": 8200,
    "cache_read_tokens": 280000,
    "model": "claude-sonnet-4-6"
  }
}
```

Implementação: ler todos os ficheiros JSONL dentro do directório `session_id`, extrair thinking blocks e tool events, agregar stats de usage.

#### `GET /api/insights-stats`

Métricas agregadas dos últimos 7 dias para um projecto.

```
Query params:
  project: str — nome do projecto

Response: {
  "sessions_count": 12,
  "total_tokens": 340000,
  "cache_hit_pct": 78,
  "top_tool": "Read",
  "top_tool_count": 142
}
```

Nota: `/api/weekly-stats` já existe mas só retorna tokens por dia (`weekly_tokens.json`). Este novo endpoint calcula as métricas adicionais (sessões, tool mais usada, cache hit %) a partir dos JSONLs da semana. É um endpoint separado para não afectar o monitor actual.

---

## Fluxo de dados — coluna live

```
1. Página carrega →
   GET /api/sessions?project=X → obter session_id da sessão activa (is_active: true)

2. GET /api/session-detail?project=X&session_id=Y →
   popular thinking (últimos 5) e tool feed (últimos 20) imediatamente

3. SSE /events →
   - evento "tool": adicionar ao topo do feed
   - evento "thinking": usar `block_id` para decidir replace vs append — se o `block_id` é igual ao último evento recebido, **substituir** o último bloco no array (actualização incremental); se o `block_id` mudou, **adicionar** novo bloco (append). O JS usa sempre `block_id` como chave, nunca a posição no array.
   - evento "stats": actualizar contador de tokens no header
```

Não existe endpoint `/api/thinking` separado — `/api/session-detail` cobre ambos os casos (live e histórico).

---

## Layout Detalhado

### Sistema de painéis — estilo IntelliJ

A página usa sidebars laterais com ícones que toggleam painéis. Quando um painel fecha, os restantes expandem para preencher o espaço (CSS flex transition).

**Sidebar esquerda** (controla coluna live):
- 🧠 Raciocínio — thinking block expansível
- 🔧 Ferramentas — feed de actividade cronológico
- ⬤ Tokens — contexto %, Input / Output / Cache rd

**Sidebar direita** (controla coluna histórico):
- 📊 Stats — grid 2×2 da semana
- 🎬 Sessões — lista de sessões recentes
- 📁 Git — ficheiros para commit

Ícone azul = painel aberto. Ícone cinzento = painel fechado.

**Exemplos de configuração:**
- Tudo aberto → layout completo (default)
- Sessões fechadas → stats e git expandem na coluna direita
- Ferramentas + Tokens fechados → thinking ocupa toda a coluna esquerda (modo leitura)

### Coluna esquerda — Live (56% da largura)

```
┌──┬──────────────────────────────────────┐
│🧠│ ● Live · <nome-do-projecto>          │
│🔧│ ┌─ 🧠 Raciocínio ─────────────────┐ │
│⬤│ │ "Primeiras linhas..."            │ │
│  │ │ ▼ ver completo (N palavras)      │ │
│  │ ├─ 🔧 Ferramentas ────────────────┤ │
│  │ │ 📖 Read   app.py      1.2s ✓    │ │
│  │ │ ⚡ Bash   git log     0.4s ✓    │ │
│  │ │ ✏️ Edit   index.html  em curso… │ │
│  │ ├─ ⬤ sonnet-4-6  39% ████░░░ ───┤ │
│  │ │ Input 14 · Output 876 · Cache 384k│ │
│  │ └──────────────────────────────────┘ │
└──┴──────────────────────────────────────┘
```

### Coluna direita — Histórico

```
┌──────────────────────────┬──┐
│ 📊 Esta semana           │📊│
│ ┌──────┐ ┌──────┐        │🎬│
│ │  12  │ │ 340k │        │📁│
│ └──────┘ └──────┘        │  │
│ ┌──────┐ ┌──────┐        │  │
│ │ Read │ │  78% │        │  │
│ └──────┘ └──────┘        │  │
├──────────────────────────┤  │
│ 🎬 Sessões recentes      │  │
│ ● claude-monitor   agora │  │
│   copilot-meeting  ontem │  │
├──────────────────────────┤  │
│ 📁 Para commit · 3 ▾    │  │
│ M  app.py                │  │
│ A  static/insights.html  │  │
└──────────────────────────┴──┘
```

### Replay de sessões

- Clicar numa sessão da lista chama `/api/session-detail?project=&session_id=`
- A coluna esquerda mostra thinking + tools dessa sessão
- Um badge "histórico · <data>" substitui "● Live"
- O SSE continua activo mas os eventos históricos não actualizam a vista — só a sessão live recebe SSE

### Modal de diff

- Abre ao clicar em qualquer ficheiro na secção "Para commit"
- Header: nome do ficheiro + badges +N / -N
- Body: diff syntax-highlighted (verde adições, vermelho remoções)
- Footer: navegação ← → entre ficheiros da lista
- Fecha com ✕ ou Escape

---

## Integração com Monitor Actual

- O monitor (`/`) não é alterado excepto um link "✦ Insights" adicionado na navbar
- O `/insights` partilha o mesmo servidor FastAPI
- O link "← Monitor" no Insights faz `window.location = '/'`

---

## Fora de Âmbito (v1)

- Autenticação
- Comparação side-by-side de duas sessões
- Exportação de dados
- Filtros avançados por tipo de tool ou período
- Notificações

---

## Critérios de Sucesso

- `/insights` carrega sem erros com dados reais de sessões activas
- O thinking do Claude é visível e expansível em tempo real
- O feed de tools actualiza via SSE sem reload
- O painel de thinking actualiza via SSE quando o Claude está a trabalhar
- Clicar numa sessão passada mostra o seu thinking e tools correctamente (via `/api/session-detail`)
- O modal de diff abre, navega entre ficheiros e fecha sem bugs
- O grid de stats mostra dados não-zero para um projecto com pelo menos um JSONL da semana actual
- O monitor actual (`/`) continua a funcionar sem alterações
