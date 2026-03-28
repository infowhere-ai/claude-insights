# claude-monitor

Monitor em tempo real do Claude Code — mostra o que o Claude está a fazer: status actual, ficheiros tocados, log de eventos.

## Stack

- Python 3.12 + FastAPI + SSE
- HTML/JS puro (sem framework, sem build step)
- Claude escreve em `.claude/status.json`, a app lê e faz broadcast via SSE

## Descrição

Aplicação web leve que serve todos os projectos InfoWhere. O Claude Code escreve o seu estado em `.claude/status.json` nos projectos, e o claude-monitor lê e transmite via SSE para qualquer browser ligado.

**v1 mostra**:
- Status actual do Claude (o que está a fazer agora)
- Ficheiros tocados na sessão com timestamp
- Log de eventos com ✅/❌
- Auto-refresh via SSE

**Evolução futura**:
- Diff de ficheiros
- Histórico de sessões
- Acesso remoto
- Vue no frontend

## Git Scopes

- `app` — aplicação principal (FastAPI + SSE)
- `frontend` — HTML/JS frontend
- `config` — configuração e deploy
