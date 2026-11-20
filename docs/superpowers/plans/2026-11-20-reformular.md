# Reformular Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tela "Reformular": colar link de canal (YouTube Shorts, TikTok, clipes da Twitch), escolher cortes prontos, refazer cada um inteiro com o kit Cat Clips e postar nas contas Postiz escolhidas (na hora ou pelo botão Publicar).

**Architecture:** O backend lista os vídeos do canal com yt-dlp e grava pedidos em `reform_items`. O gravador (`live/`) baixa um vídeo por vez com yt-dlp — só quando não há tarefa na fila, para não atrasar as lives —, cobre a faixa de baixo de vídeos em pé, sobe pelo mesmo caminho das janelas de live (`/upload` + `/tasks/`) e grava o `task_id`. No worker, um gancho antes da escolha de momentos troca a análise da IA por um segmento único do vídeo inteiro com o kit e a checagem de nudez/gore. Pronto o vídeo, o gravador cria os pedidos de postagem (se "publicar automaticamente") e posta os pedidos de clipes reformulados com as mesmas regras das lives.

**Tech Stack:** FastAPI + SQL puro (asyncpg), worker Python, gravador asyncio + psycopg, yt-dlp, ffmpeg, Next.js 15 + next-intl.

**Spec:** `docs/superpowers/specs/2026-11-20-reformular-design.md`

## Global Constraints

- Tudo que o dono lê em português; textos da tela nos 6 idiomas (pt, en, es, de, id, zh), dentro de `frontend/messages/<loc>/live.json` na chave `reform` (namespace `live` já carregado; não criar namespace novo).
- Duração máxima de um corte pronto: **180 s** (maiores aparecem desabilitados). Tag **"menos de 1 min"** quando `duration < 61` (o TikTok só monetiza acima de 60 s; mesma regra de `backend/src/clip_duration.py`).
- Lista: **30** vídeos por página.
- Fontes aceitas: YouTube (canal → aba `/shorts`), TikTok (perfil), Twitch (canal → `/clips?filter=clips&range=30d`). Instagram: erro com mensagem "Instagram ainda não".
- Postagem: só contas autorizadas e ativas do usuário; nunca repetir clipe numa conta (`live_posts` UNIQUE); CTA, borrão fail-closed, postagem segura e limite diário iguais às lives.
- Faixa que cobre a legenda antiga (só vídeo em pé, `altura > largura`): preta e opaca, de **66 %** a **88 %** da altura (valores iniciais; o dono aprova numa prévia, Task 6). Legenda Cat Clips (`position_y` 0.74) e CTA (`FINAL_CY` 1338/1920 ≈ 0.70) caem dentro dela, como no layout das lives.
- Próxima migration: `20261120_0018_reform_items.sql`.
- Nunca escrever chaves/segredos em arquivo; checar o diff contra os valores do `.env` antes de cada push.
- Rebuild: gravador (`live-recorder`) e frontend (mensagens) precisam de rebuild da imagem; backend de `docker compose restart backend` (roda a migration); worker só reinicia com `select count(*) from tasks where status='processing'` = 0.

## Review Focus

1. Link colado com lixo (`https://evil.com`, `javascript:`, perfil inexistente, canal sem vídeos) → mensagem clara, nada é baixado. Teste: `test_normalize_source_rejects_other_hosts` (Task 1).
2. O cliente manda uma `url` forjada no POST → o servidor ignora e remonta a URL a partir de `platform` + `id` validado. Teste: `test_reform_request_rebuilds_urls_and_drops_bad_ids` (Task 2).
3. 30 vídeos marcados de uma vez com lives rodando → só um vídeo de reformular no sistema por vez, e só sem tarefa na fila. Teste: `test_can_submit_waits_for_an_empty_queue` (Task 4).
4. Vídeo de 2–3 min não pode ser cortado em 60/120 s nem perder pedaços na limpeza de silêncio → duração final ≈ duração da fonte. Verificação real na Task 6.
5. Vídeo removido da plataforma / yt-dlp falha → item vira `error` com motivo, volta a ficar disponível, não trava a fila. Teste: `test_failed_download_marks_error` (Task 4).

---

### Task 1: Banco e funções de reformular no backend

**Files:**
- Create: `backend/src/migrations/sql/20261120_0018_reform_items.sql`
- Create: `backend/src/reform.py`
- Test: `backend/tests/unit/test_reform.py`

**Interfaces:**
- Produces:
  - Tabela `reform_items (id VARCHAR(36) PK, user_id VARCHAR(36) NOT NULL, platform VARCHAR(16) NOT NULL, video_id VARCHAR(100) NOT NULL, source_url TEXT NOT NULL, source_channel VARCHAR(100) NOT NULL, title TEXT NOT NULL, duration REAL, task_id VARCHAR(36) REFERENCES tasks(id) ON DELETE SET NULL, edit_kit VARCHAR(32) NOT NULL, auto_publish BOOLEAN NOT NULL DEFAULT false, target_ids TEXT[] NOT NULL DEFAULT '{}', status VARCHAR(16) NOT NULL DEFAULT 'pending' /* pending|submitted|done|error */, reason TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (user_id, platform, video_id))` + índice `(status, created_at)`.
  - `normalize_source(url: str) -> tuple[str, str, str]` → `(platform, listing_url, channel)`; `ValueError` para host fora da lista, e `ValueError("instagram")` para Instagram.
  - `valid_video_id(platform: str, video_id: str) -> bool` (YouTube `^[A-Za-z0-9_-]{11}$`, TikTok `^\d{8,25}$`, Twitch slug `^[A-Za-z0-9_-]{1,100}$`).
  - `video_url(platform: str, video_id: str, channel: str) -> str` (YouTube `https://www.youtube.com/shorts/{id}`, TikTok `https://www.tiktok.com/@{channel}/video/{id}`, Twitch `https://clips.twitch.tv/{id}`).
  - `listing_items(info: dict, platform: str) -> list[dict]` → `{id, title, duration: float|None, thumbnail: str|None}`; para Twitch o `id` é o slug tirado de `entry["url"]`.
  - `clean_title(title: str) -> str` (tira `#hashtags`, junta espaços, corta em 100).
  - `loudest_second(path: Path) -> float` (segundo de maior RMS; ffmpeg → PCM mono 8 kHz → numpy, janelas de 0,5 s).
  - `reform_segment(duration: float, title: str, kit_id: str, peak_s: float) -> dict`.
  - `async reform_segments(task_id: str, video_path: Path, file_duration: float) -> list[dict] | None` (None se a tarefa não é de reformular).

- [ ] **Step 1: Write the failing tests** em `backend/tests/unit/test_reform.py`:

```python
@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/@MrBeast", ("youtube", "https://www.youtube.com/@MrBeast/shorts", "@MrBeast")),
    ("youtube.com/@MrBeast/videos", ("youtube", "https://www.youtube.com/@MrBeast/shorts", "@MrBeast")),
    ("https://www.tiktok.com/@khaby.lame?lang=pt", ("tiktok", "https://www.tiktok.com/@khaby.lame", "khaby.lame")),
    ("https://twitch.tv/xqc", ("twitch", "https://www.twitch.tv/xqc/clips?filter=clips&range=30d", "xqc")),
])
def test_normalize_source(url, expected): assert normalize_source(url) == expected

@pytest.mark.parametrize("url", ["https://evil.com/@x", "javascript:alert(1)", "https://user:pw@youtube.com/@x", "https://www.youtube.com/watch?v=abc"])
def test_normalize_source_rejects_other_hosts(url):
    with pytest.raises(ValueError): normalize_source(url)

def test_instagram_is_named():
    with pytest.raises(ValueError, match="instagram"): normalize_source("https://www.instagram.com/khaby00/reels/")

def test_listing_items_twitch_uses_the_slug():
    info = {"entries": [{"id": "4192171452", "url": "https://clips.twitch.tv/BraveSlug-abc_1", "title": "t", "duration": 33.0}]}
    assert listing_items(info, "twitch")[0]["id"] == "BraveSlug-abc_1"

def test_video_url_and_ids():
    assert video_url("tiktok", "7709194461913419038", "khaby.lame") == "https://www.tiktok.com/@khaby.lame/video/7709194461913419038"
    assert not valid_video_id("youtube", "../etc/passwd")

def test_clean_title(): assert clean_title("Coach, trust me 👀   #learnfromkhaby #comedy") == "Coach, trust me 👀"

def test_reform_segment_covers_the_whole_video():
    s = reform_segment(150.4, "Título", "catclips", 12.0)
    assert (s["start_time"], s["end_time"], s["max_seconds"]) == ("00:00", "02:30", 150.4)
    assert s["edit_kit"] == {"id": "catclips", "peak_s": 12.0, "chat": []} and s["sensitive_check"] is True
    assert s["hook_title"] == "Título" and s["workflow_meta"]["burn_hook_title"] is False

def test_loudest_second_finds_the_loud_part(tmp_path):
    # 4 s de silêncio, 1 s de tom alto em 4–5 s, 2 s de silêncio (gerado com ffmpeg lavfi)
    ...
    assert 3.5 <= loudest_second(path) <= 5.0
```

- [ ] **Step 2:** `scripts/backend-test.sh tests/unit/test_reform.py` → FAIL (módulo não existe).
- [ ] **Step 3:** Escrever a migration e `backend/src/reform.py`. `end_time` com `seconds_to_mmss` já usado no projeto. `reform_segments` lê `SELECT title, edit_kit FROM reform_items WHERE task_id = :t`; com linha, devolve `[reform_segment(file_duration, clean_title(title), edit_kit, loudest_second(video_path))]` (o `loudest_second` roda em thread). `burn_hook_title: False` = decisão minha (o título da fonte por cima do vídeo em pé cobriria a imagem); o título continua como nome do clipe e no post.
- [ ] **Step 4:** `scripts/backend-test.sh tests/unit/test_reform.py` → PASS.
- [ ] **Step 5:** Commit `feat(reform): reform_items table and helpers`.

### Task 2: Rotas de reformular e Publicar aceitando clipes reformulados

**Files:**
- Modify: `backend/src/api/routes/live.py` (perto de `publish_clips`)
- Test: `backend/tests/unit/test_live_routes.py`

**Interfaces:**
- Consumes: Task 1 (`normalize_source`, `listing_items`, `valid_video_id`, `video_url`).
- Produces:
  - `GET /live/reform/list?url=&page=1` → `{platform, channel, items:[{id, title, duration, thumbnail, short: bool, too_long: bool, taken: bool}]}`; 422 com `detail="instagram"` ou `detail="unsupported"`; 502 se o yt-dlp falhar. yt-dlp em `run_in_threadpool` com `extract_flat="in_playlist"`, `playliststart/playlistend` para a página. Durações que vierem vazias (YouTube) são buscadas por vídeo, até 8 em paralelo, com cache em memória por id.
  - `reform_request(body: dict, kits: set[str]) -> list[dict]` (função pura; valida `items[]` com `platform`, `id`, `title`, `duration`, `channel`; descarta ids inválidos e `duration > 180`; remonta `source_url` com `video_url`; ignora qualquer `url` do cliente; 1–30 itens senão `HTTPException(422)`).
  - `POST /live/reform` `{items, edit_kit, target_ids, auto_publish}` → `{queued, skipped}`. Só `_postiz_user`. `target_ids` filtrados para contas do usuário ativas. Upsert: `ON CONFLICT (user_id, platform, video_id) DO UPDATE ... WHERE reform_items.status = 'error' OR (reform_items.status = 'done' AND reform_items.task_id IS NULL)` (repetir só depois de erro ou geração apagada).
  - `GET /live/reform/items` → 20 últimos `{id, title, platform, status, reason, task_id, created_at}`.
  - `taken` = linha existe e (`status IN ('pending','submitted')` ou (`status='done'` e `task_id IS NOT NULL`)).
  - `publish_clips`: um clipe conta se tem `live_clips` **ou** a tarefa tem `reform_items` (`not_live` passa a contar só os outros).

- [ ] **Step 1: Write the failing test** `test_reform_request_rebuilds_urls_and_drops_bad_ids`:

```python
items = [
    {"platform": "tiktok", "id": "7709194461913419038", "title": "a", "duration": 27, "channel": "khaby.lame", "url": "https://evil.com/x"},
    {"platform": "youtube", "id": "../../x", "title": "b", "duration": 30, "channel": "@a"},
    {"platform": "twitch", "id": "Slug-1", "title": "c", "duration": 200, "channel": "xqc"},
]
out = reform_request({"items": items}, {"catclips"})
assert [i["source_url"] for i in out] == ["https://www.tiktok.com/@khaby.lame/video/7709194461913419038"]
with pytest.raises(HTTPException): reform_request({"items": []}, {"catclips"})
```

- [ ] **Step 2:** `scripts/backend-test.sh tests/unit/test_live_routes.py` → FAIL.
- [ ] **Step 3:** Implementar `reform_request`, as três rotas e a mudança em `publish_clips`.
- [ ] **Step 4:** Mesmo comando → PASS (as 46 falhas de Postgres/Redis em outros arquivos não contam).
- [ ] **Step 5:** Commit `feat(reform): list, queue and publish routes`.

### Task 3: Gancho no worker (vídeo inteiro com o kit)

**Files:**
- Modify: `backend/src/services/video_service.py` (bloco `if workflow == "highlights":`, ~linha 1614)
- Test: `backend/tests/unit/test_reform.py`

**Interfaces:**
- Consumes: `reform_segments(task_id, video_path, file_duration)` (Task 1).

- [ ] **Step 1: Write the failing test** `test_reform_segment_keeps_full_length_through_render_ranges`: `resolve_render_ranges(video_path_fake, reform_segment(150.0, "t", "catclips", 3.0), cleanup_settings=None, max_clip_seconds=60.0).bounds == (0.0, 150.0)` (se precisar de arquivo real, usar um mp4 lavfi de 150 s gerado no teste).
- [ ] **Step 2:** Rodar → FAIL se o segmento for cortado em 60 s; ajustar `reform_segment` (ex.: `max_seconds`, `planned`) até passar sem mexer no caminho das lives.
- [ ] **Step 3:** No `video_service`, antes de `_select_highlight_segments`: `reform = await reform_segments(task_id, video_path, file_duration)`; se não for `None`, usar `segments_json = reform`, `clip_limit = 1`, `relevant_parts = None`, `final_plan = True` e pular a seleção da IA. Se a tarefa tiver limpeza de silêncio ligada, desligar para essa tarefa (o vídeo é um corte pronto).
- [ ] **Step 4:** `scripts/backend-test.sh tests/unit/test_reform.py tests/unit/test_live_scoring.py tests/unit/test_edit_kit_render.py` → PASS.
- [ ] **Step 5:** Commit `feat(reform): whole-video segment instead of AI selection`.

### Task 4: Gravador baixa, cobre a faixa, envia e acompanha

**Files:**
- Modify: `live/requirements.txt` (adicionar `yt-dlp==2026.10.21`, mesma versão do backend)
- Create: `live/supoclip_live/reform.py`
- Modify: `live/supoclip_live/service.py` (`cycle()` chama `await self.reform()` antes de `post_clips`)
- Test: `live/tests/test_reform.py`

**Interfaces:**
- Consumes: tabela `reform_items` (Task 1); `self.backend.upload/create_task` existentes.
- Produces:
  - `can_submit(queued_tasks: int, in_flight: int) -> bool` (`queued_tasks == 0 and in_flight == 0`).
  - `band_filter(width: int, height: int, top: float = 0.66, bottom: float = 0.88) -> str | None` (`None` para vídeo deitado; `drawbox=x=0:y=<par>:w=iw:h=<par>:color=black@1:t=fill` para em pé).
  - `download(url: str, dest: Path) -> dict` (yt-dlp em thread, mp4, sem playlist; devolve `{width, height, duration}`).
  - `Live.reform()`: (1) itens `submitted` cuja tarefa terminou → `done` se tem clipe, senão `error` com motivo; se `done` e `auto_publish`, inserir `live_post_requests` para cada clipe × conta de `target_ids` ainda ativa (`ON CONFLICT DO NOTHING`); (2) se `can_submit(<tarefas queued/pending do usuário>, <itens submitted com tarefa pending/queued/processing>)`, pegar o `pending` mais antigo, baixar, aplicar `band_filter` com ffmpeg, `upload` (chave `reform-upload-{id}`), `create_task` (chave `reform-task-{id}`, `clip_length="auto"`), gravar `task_id` + `submitted`; erro de download/ffmpeg → `error` com motivo; erro transitório do backend → fica `pending`. Apagar os arquivos temporários sempre.

- [ ] **Step 1: Write the failing tests** em `live/tests/test_reform.py`:

```python
def test_can_submit_waits_for_an_empty_queue():
    assert can_submit(0, 0) and not can_submit(1, 0) and not can_submit(0, 1)

def test_band_filter_only_for_vertical():
    assert band_filter(1920, 1080) is None
    assert band_filter(1080, 1920) == "drawbox=x=0:y=1266:w=iw:h=424:color=black@1:t=fill"  # 0.66*1920 e 0.22*1920, pares

def test_failed_download_marks_error(monkeypatch):
    # Live com fetch falso: 1 item pending; download levanta yt_dlp DownloadError
    # → UPDATE reform_items SET status='error', reason=... ; nenhum upload
```

- [ ] **Step 2:** `/tmp/claude-1000/ltest.sh tests/test_reform.py` → FAIL.
- [ ] **Step 3:** Implementar `reform.py` e `Live.reform()`.
- [ ] **Step 4:** `/tmp/claude-1000/ltest.sh tests` → todos PASS.
- [ ] **Step 5:** Commit `feat(reform): recorder downloads, covers old captions and submits one at a time`.

### Task 5: Gravador posta os clipes reformulados

**Files:**
- Modify: `live/supoclip_live/service.py` (`post_clips`)
- Modify: `live/supoclip_live/postiz.py` (`caption`)
- Test: `live/tests/test_postiz.py`, `live/tests/test_post_blur.py`

**Interfaces:**
- Produces: `caption(hook_title, channel, live_title, platform)` aceita `platform="tiktok"` → fonte `tiktok.com/@{channel}`; YouTube de reformular usa o handle (`youtube.com/@canal`).
- `post_clips`: segunda busca `reform_candidates` (pedidos em `live_post_requests` de clipes cuja tarefa tem `reform_items`), com as mesmas colunas que o laço usa: `channel = ri.source_channel`, `platform = ri.platform`, `edit_kit = ri.edit_kit`, `post_visibility = 'public'`, `live_title = NULL`, `channel_blur = TRUE`, `channel_safe = FALSE`, `channel_id = NULL`, `clip_created = g.created_at`, mais as colunas da conta e do `live_clip_sensitive`. Junta com a lista das lives (pedidos primeiro) e segue o mesmo laço.

- [ ] **Step 1: Write the failing tests**: `test_caption_credits_tiktok_source` (`"🎮 tiktok.com/@khaby.lame"` e `#tiktok` no texto); `test_post_clips_posts_a_reform_request` (fetch falso devolve uma linha de reformular → `postiz.upload` chamado, `record_post(..., "posted", ...)`).
- [ ] **Step 2:** `/tmp/claude-1000/ltest.sh tests` → FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `/tmp/claude-1000/ltest.sh tests` → PASS; rodar a SQL nova no Postgres real (como feito para `live_post_requests`) e confirmar que executa.
- [ ] **Step 5:** Commit `feat(reform): post reformulated clips like live clips`.

### Task 6: Tela Reformular, colocar no ar e prévia da faixa

**Files:**
- Create: `frontend/src/app/reformular/layout.tsx` (igual a `frontend/src/app/live/layout.tsx`)
- Create: `frontend/src/app/reformular/page.tsx`
- Modify: `frontend/src/components/app/app-shell.tsx:42` (item `{ href: "/reformular", labelKey: "reform", icon: Repeat2, match: p => p.startsWith("/reformular"), mobile: false, live: true }`)
- Modify: `frontend/messages/<loc>/shell.json` (`nav.reform`: pt "Reformular", en "Remake", es "Reformular", de "Neu schneiden", id "Olah ulang", zh "二次创作") e `frontend/messages/<loc>/live.json` (chave `reform`)

**Interfaces:**
- Consumes: `GET /api/live/reform/list`, `POST /api/live/reform`, `GET /api/live/reform/items`, `GET /api/live/edit-kits`, `GET /api/live/postiz/accounts` (pelo proxy existente `/api/live/[...path]`).

- [ ] **Step 1:** Página conforme o esboço do spec: campo + Buscar; lista com capa, título, duração, tags "menos de 1 min" / "longo demais" / "já reformulado ✓"; "carregar mais"; seletor de kit (padrão `catclips`); contas ativas e sem problema, todas marcadas; switch "Publicar automaticamente ao terminar" (desligado por padrão); botão "Reformular N vídeos"; lista "Em andamento / prontos" atualizando a cada 15 s com link para `/tasks/<id>`. Mensagens de erro para `instagram`, `unsupported` e 502. Página sem acesso a live mostra o mesmo bloqueio do `/live` (`useLiveAccess`).
- [ ] **Step 2:** `docker exec supoclip-frontend sh -c "npx tsc --noEmit -p . | grep reformular; npx eslint src/app/reformular src/components/app/app-shell.tsx"` → sem erros.
- [ ] **Step 3:** Deploy: `docker compose restart backend` (migration 0018); rebuild `live-recorder` e `frontend` com `DOCKER_BUILDKIT=0 COMPOSE_BAKE=false docker compose build live-recorder frontend`, `docker compose --profile live up -d live-recorder frontend`; reiniciar o worker só com 0 tarefas `processing`. Conferir `\d reform_items` e logs sem erro.
- [ ] **Step 4: Prévia para o dono.** Reformular 1 vídeo em pé do TikTok e 1 clipe deitado da Twitch, **sem** publicar automaticamente. Cortar os primeiros 6 s e os últimos 6 s de cada (para ver faixa, legenda e CTA), mandar ao dono e esperar a aprovação da faixa. Ajustar `top/bottom` em `band_filter` se ele pedir, rebuild do gravador e repetir.
- [ ] **Step 5:** Conferir: os 2 aparecem na Biblioteca; duração final ≈ fonte (±1 s); o botão Publicar da Biblioteca aceita os dois (resposta com `not_live: 0`).
- [ ] **Step 6:** Commit `feat(reform): Reformular page`; checar segredos do `.env` contra o diff; push para `origin` e `brainsidian` (`feat/live-clipping` e `main`).
