# Limite só nas lives gravando — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (owner chose inline execution). Steps use `- [ ]`.

**Goal:** Seguir canais sem limite; o dono escolhe quantas lives gravam ao mesmo tempo, vê vagas/espera e recebe aviso
de processador ou disco apertados; dados no disco Jogos.

**Architecture:** Tabela nova `live_accounts` (número por conta). Backend tira o teto de canais e ganha
`GET/PATCH /live/slots`. O robô lê o número da conta junto com os canais e usa `slot_free`. Componente novo
`recording-slots.tsx` na página de Lives. Disco: `docker-compose.override.yml` local.

**Spec:** `docs/superpowers/specs/2026-11-22-limite-de-lives-design.md`

## Global Constraints

- Número da conta: padrão 10; mínimo 1; máximo = teto da máquina `LIVE_MAX_RECORDINGS` (`.env` vai para 24).
- Divisão justa entre contas continua (`fair_share`).
- Avisos: `cpu` quando média de 5 min > 75% dos núcleos; `disk` quando o disco de `/app/uploads` tem < 50 GB livres.
- Aviso só informa; nunca muda o número.
- Textos nos 6 idiomas de `frontend/messages/*/live.json`.
- Árvore compartilhada com mudanças não commitadas de outras sessões (`page.tsx`, `live.json`, `service.py`,
  `docker-compose.yml`): stage só os meus trechos (patch contra HEAD + `git apply --cached`).
- Override do disco fica fora do git (`.git/info/exclude`).

## Review Focus

1. Conta sem linha em `live_accounts` → 10 vagas (como hoje). Teste `test_slot_free_*` com `None`.
2. Número acima do teto ou abaixo de 1 → 422. Teste `test_slots_limit_rejects_out_of_range`.
3. Dono baixa o número com lives gravando → nenhuma é cortada; só novas esperam. Teste `test_slot_free_lower_limit_keeps_running`.
4. Disco Jogos não montado → container não sobe (pasta de origem não existe). Verificação manual na Task 4.
5. Adicionar o 21º canal → aceito. Teste `test_add_channel_has_no_follow_cap`.

---

### Task 1: Backend — sem teto de canais; `/live/slots`

**Files:** Create `backend/src/migrations/sql/20261122_0020_live_accounts.sql`; Modify `backend/src/api/routes/live.py`,
`docker-compose.yml` (backend env `LIVE_MAX_RECORDINGS`); Test `backend/tests/unit/test_live_routes.py`

- Tabela `live_accounts(user_id VARCHAR PRIMARY KEY, max_recordings INTEGER NOT NULL DEFAULT 10, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`.
- Sai `MAX_CHANNELS_PER_USER` (checagem em `add_channel`, `at_limit`/`limit` em `trending`).
- `RECORDING_CEILING = max(1, int(os.getenv("LIVE_MAX_RECORDINGS", "10")))`; `DEFAULT_RECORDINGS = 10`.
- `slot_warnings(load5: float, cpus: int, free_gb: float) -> list[str]` → subconjunto ordenado de `["cpu", "disk"]`.
- `GET /live/slots` → `{"recording": int, "waiting": int, "limit": int, "ceiling": int, "warnings": [...], "cpu_percent": int, "disk_free_gb": int}`
  (carga via `os.getloadavg()[1]`, disco via `shutil.disk_usage("/app/uploads")`).
- `PATCH /live/slots` `{"limit": n}` → inteiro 1..ceiling (bool não), upsert; senão 422.
- [ ] Testes `test_slot_warnings` (`(12.1,16,49.9)`→`["cpu","disk"]`, `(12.0,16,50)`→`[]`), `test_slots_limit_rejects_out_of_range`
  (0, ceiling+1, True, "5" → 422), `test_add_channel_has_no_follow_cap` (`not hasattr(live, "MAX_CHANNELS_PER_USER")`) → FAIL → PASS.
- [ ] `docker compose restart backend`; conferir tabela. Commit `feat(live): follow any number of channels; choose recording slots`.

### Task 2: Robô — número da conta

**Files:** Modify `live/supoclip_live/service.py` (`CHANNELS_SQL` com `LEFT JOIN live_accounts a ... a.max_recordings`; `watch`);
Test `live/tests/test_service.py`

- `slot_free(total: int, mine: int, account_limit: int | None, owners: int) -> bool`:
  `total < MAX_RECORDINGS and mine < min(account_limit or 10, fair_share(MAX_RECORDINGS, owners))`.
- [ ] Testes `test_slot_free_uses_the_account_number`, `test_slot_free_defaults_to_ten`, `test_slot_free_lower_limit_keeps_running`
  (só decide entradas novas) → FAIL → PASS (`/tmp/claude-1000/ltest.sh tests`).
- [ ] `.env` `LIVE_MAX_RECORDINGS=24`; rebuild do robô de export limpo do HEAD; up. Commit `feat(live): recording slots follow the account's number`.

### Task 3: Página de Lives — painel e aviso

**Files:** Create `frontend/src/app/live/recording-slots.tsx`; Modify `frontend/src/app/live/page.tsx` (logo após a barra de
contagens; no HEAD, logo após o cabeçalho), `frontend/messages/*/live.json` (chave `slots`)

- Mostra "Gravando agora: X de [N]" (`NumberField` 1..ceiling, PATCH ao salvar), "· Y aguardando vaga" se Y > 0,
  e um `Alert` âmbar por aviso: cpu ("Processador apertado: {percent}% em uso") e disco ("Pouco disco: {gb} GB livres").
  Atualiza a cada 30 s.
- [ ] `npx tsc --noEmit` sem erros novos; conferir no navegador. Commit `feat(live): recording slots panel with warnings`.

### Task 4: Dados no disco Jogos

- [ ] `rsync -a` dos volumes `supoclip_uploads` e `supoclip_live_chunks` para `/var/mnt/jogos/supoclip/{uploads,live_chunks}` com tudo rodando.
- [ ] `docker-compose.override.yml` (backend, worker, ai-worker, mcp/uploads conforme o compose; live-recorder `/data`) com bind + `security_opt: [label=disable]`; `.git/info/exclude`.
- [ ] Esperar 0 tarefas `processing`; `docker compose --profile live stop`; `rsync -a --delete` final; `up -d`; conferir clipes abrindo e lives retomando.
- [ ] Revisão final (opus), conferir segredos do `.env` no diff, push `origin` e `brainsidian` (`feat/live-clipping` e `main`).
