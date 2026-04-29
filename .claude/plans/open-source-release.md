# Plano — Open Source Release (claude-insights)

> Criado: 2026-04-29
> Objectivo: publicar claude-insights como repo público na org `infowhere-ai`

---

## Contexto

O CI/CD já está preparado para open source:
- `release.yml` já referencia `infowhere-ai` org
- PyPI usa OIDC trusted publishing (sem token de API necessário)
- Homebrew tap já configurado para `infowhere-ai/homebrew-claude-insights`
- GitHub Releases já configuradas

---

## Passos

### PASSO 1 — Criar a org pública `infowhere-ai` no GitHub
**Onde**: github.com → + → New organization → Free plan
- Nome: `infowhere-ai`
- Email de contacto: o teu email
- Tipo: Public

### PASSO 2 — Criar o repo público `claude-insights`
**Onde**: github.com/infowhere-ai → New repository
- Nome: `claude-insights`
- Visibilidade: **Public**
- Licença: MIT (seleccionar no momento da criação)
- Não inicializar com README (vamos fazer push do código)

### PASSO 3 — Criar o repo Homebrew tap
**Onde**: github.com/infowhere-ai → New repository
- Nome: `homebrew-claude-insights`
- Visibilidade: **Public**
- Inicializar com README
- Criar estrutura: `Formula/claude-insights.rb` (ficheiro vazio por agora)

### PASSO 4 — Configurar PyPI Trusted Publishing
**Onde**: pypi.org → conta → Your projects → Publishing → Add a new pending publisher
- Package name: `claude-insights`
- Owner: `infowhere-ai`
- Repository: `claude-insights`
- Workflow: `release.yml`
- Environment: `pypi`

> Nota: OIDC trusted publishing — não precisa de token de API. O GitHub Actions autentica directamente no PyPI.

### PASSO 5 — Criar GitHub Secret `HOMEBREW_TAP_TOKEN`
**Onde**: github.com/infowhere-ai/claude-insights → Settings → Secrets → Actions
- Nome: `HOMEBREW_TAP_TOKEN`
- Valor: Personal Access Token com permissão `repo` na org `infowhere-ai`

> O token permite que o workflow do `claude-insights` faça push no repo `homebrew-claude-insights`.

### PASSO 6 — Criar o environment `pypi` no GitHub
**Onde**: github.com/infowhere-ai/claude-insights → Settings → Environments → New environment
- Nome: `pypi`
- Sem regras de protecção adicionais (opcional)

### PASSO 7 — Auditoria de referências internas no código
**Antes de fazer push**, verificar se existe alguma referência a:
- `infowhere-be` (org privada)
- `nexus.infowhere.be` (Nexus privado)
- `ssh.infowhere.be` (servidor privado)
- IPs internos ou credenciais

> Claude faz esta auditoria — não é manual.

### PASSO 8 — Adicionar remote e fazer push
```bash
git remote add public git@github.com:infowhere-ai/claude-insights.git
git push public main
git push public develop
```

### PASSO 9 — Fazer o primeiro release
```bash
# No repo privado (develop):
# 1. Garantir que pyproject.toml tem a versão correcta (ex: 0.1.0)
# 2. Merge develop → main
# 3. Push main → remote público
# O CI/CD release.yml dispara automaticamente
```

---

## Estado

| Passo | Status |
|-------|--------|
| 1. Criar org `infowhere-ai` | ⬜ Pendente |
| 2. Criar repo `claude-insights` | ⬜ Pendente |
| 3. Criar repo `homebrew-claude-insights` | ⬜ Pendente |
| 4. Configurar PyPI Trusted Publishing | ⬜ Pendente |
| 5. Criar secret `HOMEBREW_TAP_TOKEN` | ⬜ Pendente |
| 6. Criar environment `pypi` no GitHub | ⬜ Pendente |
| 7. Auditoria de referências internas | ⬜ Pendente |
| 8. Push para repo público | ⬜ Pendente |
| 9. Primeiro release | ⬜ Pendente |
