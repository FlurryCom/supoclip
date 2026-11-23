# Enquadramento por canal: plano de implementação

> **Para agentes:** SUB-SKILL OBRIGATÓRIO: use superpowers:subagent-driven-development (recomendado) ou
> superpowers:executing-plans para implementar este plano tarefa por tarefa. Os passos usam checkbox (`- [ ]`).

**Objetivo:** o dono marca, por canal de live, as caixas da webcam e do gameplay. O render do Cat Clips v2
corta exatamente essas regiões, confere a cada clipe se elas ainda batem e, quando o layout muda, pede ao
Claude que confirme e troca a caixa da webcam sozinho.

**Arquitetura:** as caixas ficam em `live_channels` (frações 0–1). O `live_scoring` só põe os ids
(`channel_id`, `recording_id`) no `segment["edit_kit"]`. Toda a lógica de enquadramento (reler as caixas,
checagem rápida, Claude, troca, frame de referência) mora num módulo novo, `backend/src/media/framing.py`,
chamado por `plan_streamer_split` no thread do render, com `sync_db`. A tela ganha `framing-editor.tsx`.

**Tecnologias:** FastAPI + SQL cru, `sync_db` (asyncpg no thread), OpenCV/numpy, SDK `anthropic` (síncrono),
Next.js 15 + next-intl, vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-11-21-enquadramento-por-canal-design.md`

## Restrições globais

- Caixa = `{"x","y","w","h"}` em frações do frame da fonte; `0 ≤ x,y`, `w,h ≥ 0.04`, `x+w ≤ 1`, `y+h ≤ 1`.
  `cam_box` e `game_box` são salvos juntos ou limpos juntos (`null`).
- Só o kit `catclips_v2` (layout `streamer_split`) usa as caixas. Sem caixas → comportamento de hoje, byte a byte.
- O jogo segue "inteiro, sem cortar, centralizado sobre a cópia borrada"; só a região de origem muda.
- Claude: modelo `claude-sonnet-5-5`, sobreposto por `FRAMING_CHECK_MODEL`; timeout 20 s; saída sem campos
  opcionais: `current_ok: bool`, `proposal_ok: bool`, `reason: str`.
- Chamada ao Claude: quando `framing_checked_at IS NULL`, ou quando a checagem falhou e a última conferência
  tem mais de 30 min. Reserva atômica por `UPDATE ... RETURNING`; em erro, a reserva é desfeita.
- Troca automática só da caixa da webcam, só se o Claude reprovar a atual **e** aprovar a proposta, e só
  se `framing_auto_off` for falso.
- **Mudança em relação à spec (decisão do plano):** o frame de referência fica numa coluna `bytea` de
  `live_channels`, não no bucket. São ~100 KB por canal, funciona com `MEDIA_STORAGE=local` e com `s3`, e
  continua no PostgreSQL (a regra de não guardar nada em `TEMP_DIR`). O endpoint devolve JSON com data URL,
  porque o proxy `/api/live/[...path]` só repassa texto.
- Repositório público: nada de ids reais, e-mails ou nomes de canal em testes ou commits.
- A branch `feat/live-clipping` tem mudanças de outra pessoa ainda não commitadas: cada commit faz `git add`
  só dos arquivos da tarefa.

## Pontos de atenção na revisão

1. **Fonte mais estreita que 16:9 ou com outra resolução que o frame marcado:** as frações viram pixels pela
   resolução de cada fonte, e um teste com 1280×720 e 1920×1080 garante o mesmo recorte relativo (Tarefa 2).
2. **Caixa da webcam em cima de uma borda, que vira pixel ímpar ou passa da borda:** arredondar para par e
   prender dentro do frame (Tarefa 2).
3. **Sem `ANTHROPIC_API_KEY` no worker:** nunca quebra o render; o clipe usa as caixas do canal e fica um log
   (Tarefa 3).
4. **Canal mudou de kit para outro que não o v2 com caixas salvas:** as caixas são ignoradas, nada quebra
   (Tarefa 4).
5. **Dono salva as caixas enquanto uma janela está renderizando:** a janela relê as caixas na hora do plano,
   e a próxima já pega as novas; a troca automática nunca sobrescreve uma caixa que o dono salvou depois da
   leitura (o `UPDATE` da troca compara a caixa antiga) (Tarefa 3).

---

### Tarefa 1: Colunas, validação, undo e endpoint do frame

**Arquivos:**
- Criar: `backend/src/migrations/sql/20261121_0019_live_channel_framing.sql`
- Modificar: `backend/src/api/routes/live.py` (`_settings`, `list_channels`, `update_channel`, rotas novas)
- Teste: `backend/tests/unit/test_live_routes.py`

**Interfaces:**
- Produz: colunas `live_channels.cam_box jsonb`, `game_box jsonb`, `framing_previous jsonb`,
  `framing_changed_at timestamptz`, `framing_frame bytea`, `framing_frame_at timestamptz`;
  `live_recordings.framing_checked_at timestamptz`, `framing_auto_off boolean not null default false`.
- Produz: `_box(value: Any, name: str) -> dict | None` em `live.py` (valida uma caixa; `None` aceita).
- Produz: a listagem de canais devolve também `cam_box`, `game_box`, `framing_changed_at`,
  `framing_frame_at`, `has_framing_previous: bool`.
- Produz: `POST /live/channels/{id}/framing/undo` → `{"id", "cam_box", "game_box"}`;
  `GET /live/channels/{id}/framing-frame` → `{"image": "data:image/jpeg;base64,...", "at": iso}` ou 404.

- [ ] **Passo 1: escrever os testes que falham**

```python
def test_settings_accept_framing_boxes():
    box = {"x": 0.7, "y": 0.02, "w": 0.28, "h": 0.36}
    game = {"x": 0, "y": 0, "w": 1, "h": 1}
    assert _settings({"cam_box": box, "game_box": game}) == {"cam_box": box, "game_box": game}
    assert _settings({"cam_box": None, "game_box": None}) == {"cam_box": None, "game_box": None}

@pytest.mark.parametrize("data", [
    {"cam_box": {"x": 0.9, "y": 0, "w": 0.2, "h": 0.3}, "game_box": {"x": 0, "y": 0, "w": 1, "h": 1}},  # x+w > 1
    {"cam_box": {"x": 0, "y": 0, "w": 0.01, "h": 0.3}, "game_box": {"x": 0, "y": 0, "w": 1, "h": 1}},  # w < 0.04
    {"cam_box": {"x": 0, "y": 0, "w": 0.2}, "game_box": {"x": 0, "y": 0, "w": 1, "h": 1}},  # missing h
    {"cam_box": {"x": 0, "y": 0, "w": 0.2, "h": 0.3}},  # only one of the two
    {"cam_box": {"x": True, "y": 0, "w": 0.2, "h": 0.3}, "game_box": {"x": 0, "y": 0, "w": 1, "h": 1}},
])
def test_settings_reject_bad_framing(data):
    with pytest.raises(HTTPException) as err:
        _settings(data)
    assert err.value.status_code == 422
```

- [ ] **Passo 2:** `cd backend && .venv/bin/pytest --no-cov tests/unit/test_live_routes.py -k framing -v`.
  Esperado: FAIL (as chaves são ignoradas e `_settings` devolve `{}`).

- [ ] **Passo 3: migração e rotas**
  - A migração faz `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` para as 8 colunas, com um comentário de uma
    linha como as outras migrações.
  - `_settings`: aceita `cam_box`/`game_box` só os dois juntos, via `_box` (números não booleanos, limites das
    Restrições, só as chaves x/y/w/h), e guarda como dict.
  - `update_channel`: quando há caixas, faz o bind com `CAST(:cam_box AS jsonb)` (`json.dumps`) e acrescenta
    `framing_changed_at = NULL`. Na mesma transação faz
    `UPDATE live_recordings SET framing_auto_off = true WHERE channel_id = :id AND ended_at IS NULL`.
  - `undo`: `UPDATE live_channels SET cam_box = framing_previous->'cam_box', game_box = framing_previous->'game_box',
    framing_previous = NULL, framing_changed_at = NULL WHERE id AND user_id AND framing_previous IS NOT NULL
    RETURNING ...`. 404 se nada mudou. Liga `framing_auto_off` como acima.
  - `framing-frame`: lê `framing_frame, framing_frame_at` do canal do dono; 404 se for nulo; devolve
    base64.

- [ ] **Passo 4:** rodar o mesmo comando. Esperado: PASS, e o arquivo inteiro continua verde.

- [ ] **Passo 5: commit**
```bash
git add backend/src/migrations/sql/20261121_0019_live_channel_framing.sql backend/src/api/routes/live.py backend/tests/unit/test_live_routes.py
git commit -m "feat(live): store webcam and gameplay boxes per channel"
```

---

### Tarefa 2: Caixas para pixels e split com as caixas do canal

**Arquivos:**
- Criar: `backend/src/media/framing.py` (só as funções puras nesta tarefa)
- Modificar: `backend/src/media/streamer_split.py` (`build_split_filter` ganha `game_rect`)
- Teste: `backend/tests/unit/test_framing.py`, `backend/tests/unit/test_streamer_split.py`

**Interfaces:**
- Produz: `Rect = Tuple[int, int, int, int]`; `box_to_rect(box: dict, width: int, height: int) -> Rect`
  (pares, dentro do frame).
- Produz: `rect_to_box(rect: Rect, width: int, height: int) -> dict` (frações com 4 casas).
- Produz: `quick_check(frames: list[np.ndarray], faces: Sequence[Tuple[float, Sequence[Box]]], cam: Rect,
  game: Rect) -> bool`.
- Produz: `build_split_filter(width, height, face, title, cam_rect=None, game_rect: Optional[Rect] = None) -> str`.
  Com `game_rect`, o `[game_src]` é `crop` dessa região antes do fit/blur; o topo sem webcam usa
  `game_crop` dentro dela.

- [ ] **Passo 1: escrever os testes que falham** (`test_framing.py`)

```python
def test_box_to_rect_is_even_and_inside():
    # rule: round each value to the nearest pixel, then down to even; then clamp inside the frame
    assert box_to_rect({"x": 0.7, "y": 0.02, "w": 0.28, "h": 0.36}, 1920, 1080) == (1344, 22, 538, 388)
    x, y, w, h = box_to_rect({"x": 0.999, "y": 0.999, "w": 0.04, "h": 0.04}, 1920, 1080)
    assert x + w <= 1920 and y + h <= 1080 and w % 2 == h % 2 == x % 2 == y % 2 == 0

def test_same_box_same_relative_crop_at_any_resolution():
    box = {"x": 0.25, "y": 0.5, "w": 0.5, "h": 0.25}
    assert box_to_rect(box, 1280, 720) == (320, 360, 640, 180)
    assert box_to_rect(box, 1920, 1080) == (480, 540, 960, 270)

def test_rect_to_box_round_trips():
    assert rect_to_box((480, 540, 960, 270), 1920, 1080) == {"x": 0.25, "y": 0.5, "w": 0.5, "h": 0.25}
```

`quick_check`, com frames sintéticos 1920×1080 `float32`: a webcam (0,0,480,360) quase constante (ruído
±1) e o resto com ruído forte, diferente em cada frame:
- `test_quick_check_passes_when_face_sits_in_a_still_cam`: rosto (120,80,160,180) em 8 de 8 amostras → `True`.
- `test_quick_check_fails_when_no_face_in_cam`: rosto em (1500,700,…) → `False`.
- `test_quick_check_fails_when_cam_moves_like_the_game`: o mesmo ruído forte dentro da webcam → `False`.
- `test_quick_check_passes_on_a_still_scene`: tudo constante → `True` (não dá para decidir).

Em `test_streamer_split.py`:
```python
def test_split_filter_crops_the_game_box():
    graph = build_split_filter(W, H, CAM, "x", cam_rect=(0, 600, 480, 360), game_rect=(480, 0, 1440, 1080))
    assert "[game_src]crop=1440:1080:480:0" in graph and "crop=480:360:0:600" in graph

def test_split_filter_without_game_rect_is_unchanged():
    assert build_split_filter(W, H, CAM, "x") == build_split_filter(W, H, CAM, "x", game_rect=None)
```

- [ ] **Passo 2:** `.venv/bin/pytest --no-cov tests/unit/test_framing.py tests/unit/test_streamer_split.py -v`.
  Esperado: FAIL (módulo e argumento não existem).

- [ ] **Passo 3: implementar**
  - `quick_check` passa se: (a) em ≥ metade das amostras há um rosto com centro dentro de `cam`; e
    (b) a média de `|frame[i+1]-frame[i]|` dentro de `cam` dividida pela de `game` fica abaixo de
    `CAM_STILL_RATIO = 0.5`. Se a mudança média em `game` for < 4 níveis de cinza (cena parada), passa sem
    olhar (b), mas (a) continua valendo. Se `game` contém `cam` (jogo = tela toda), a região de `game`
    usada na razão exclui `cam` (máscara).
  - Ajustar os limiares só se os testes sintéticos pedirem, e anotar no código o motivo em uma linha.
  - Em `build_split_filter`, com `game_rect` o grafo fica `[game_src]crop=w:h:x:y,split=2[game_bg][game_fg]`.

- [ ] **Passo 4:** mesmo comando. Esperado: PASS (os testes antigos do split continuam verdes).

- [ ] **Passo 5: commit**
```bash
git add backend/src/media/framing.py backend/src/media/streamer_split.py backend/tests/unit/test_framing.py backend/tests/unit/test_streamer_split.py
git commit -m "feat(kit): crop Cat Clips v2 from the channel's webcam and gameplay boxes"
```

---

### Tarefa 3: Verificação pelo Claude e a tabela de decisão

**Arquivos:**
- Modificar: `backend/src/media/framing.py`
- Teste: `backend/tests/unit/test_framing.py`

**Interfaces:**
- Consome: `box_to_rect`, `rect_to_box`, `quick_check` (Tarefa 2); `sync_db.fetchrow/execute` (`$1` params).
- Produz: `@dataclass Verdict(current_ok: bool, proposal_ok: bool, reason: str)`.
- Produz: `ask_claude(frame_jpg: bytes, cam: Rect, game: Rect, proposal: Optional[Rect]) -> Verdict`. Desenha
  as caixas (webcam em vermelho, jogo em azul) com OpenCV. Manda 1 imagem, ou 2 quando há proposta.
  Força uma ferramenta `report_framing` com `input_schema` dos três campos, todos obrigatórios. Levanta exceção
  em erro, timeout ou sem chave.
- Produz: `@dataclass FramingDecision(cam: Optional[Rect], game: Optional[Rect], source: str, reason: str)`,
  com `source` sendo um de `"channel" | "proposal" | "detector"`.
- Produz: `decide(state: dict, passed: bool, proposal: Optional[Rect], width: int, height: int,
  claim: Callable[[bool], bool], verdict: Callable[[], Verdict], swap: Callable[[dict], bool],
  flag: Callable[[], None], release: Callable[[], None]) -> FramingDecision`. É pura, com os efeitos
  injetados, e implementa a tabela da spec. `state` traz `cam_box`, `game_box`, `framing_auto_off`.
  `claim(failed)` faz a reserva. `swap(new_cam_box)` faz o
  `UPDATE live_channels SET framing_previous = jsonb_build_object('cam_box', cam_box, 'game_box', game_box),
  cam_box = $new, framing_changed_at = now() WHERE id = $id AND cam_box = $old` e devolve se mudou.
  `flag()` grava só `framing_changed_at = now()`. `release()` desfaz a reserva.

- [ ] **Passo 1: escrever os testes que falham.** Um teste por linha da tabela, com `claim`, `verdict`,
  `swap`, `flag` e `release` falsos que registram as chamadas:
  - `test_passed_and_no_claim_uses_channel_boxes`: `claim → False` → `source == "channel"`, `verdict` não é chamado.
  - `test_failed_without_claim_still_uses_channel_boxes` (Claude conferiu há < 30 min).
  - `test_claude_ok_keeps_channel_boxes`.
  - `test_claude_approves_proposal_swaps_only_the_cam`: `swap` recebe `rect_to_box(proposal)`, e o `game`
    da decisão é o `game_box` do canal → `source == "proposal"`.
  - `test_auto_off_uses_proposal_without_swapping`: `swap` não é chamado e `flag` é chamado.
  - `test_no_good_proposal_flags_and_uses_detector`: `cam is None`, `source == "detector"`, `flag` chamado.
  - `test_claude_error_releases_and_keeps_channel_boxes`: `verdict` levanta exceção → `release` chamado,
    `source == "channel"`.
  - `test_swap_lost_race_keeps_channel_boxes`: `swap → False` (o dono salvou no meio) → `source == "channel"`.
  - `test_ask_claude_sends_two_images_with_a_proposal`: com um cliente `anthropic` falso, checa que vão 2
    blocos `image`, o modelo vem de `FRAMING_CHECK_MODEL` e a resposta da ferramenta vira `Verdict`.

- [ ] **Passo 2:** `.venv/bin/pytest --no-cov tests/unit/test_framing.py -v`. Esperado: FAIL.

- [ ] **Passo 3: implementar `Verdict`, `FramingDecision`, `ask_claude` e `decide`.**
  - O SQL da reserva (`claim`) é o da spec, com `$2::boolean` para `failed`. A reserva devolve o
    `framing_checked_at` anterior, que o `release` usa para restaurar.
  - O prompt (texto a cargo do construtor) explica: vermelho = webcam do streamer, azul = gameplay. A
    pergunta é se o vermelho mostra a pessoa inteira, sem pegar o jogo, e se o azul mostra a tela do jogo.

- [ ] **Passo 4:** mesmo comando. Esperado: PASS.

- [ ] **Passo 5: commit**
```bash
git add backend/src/media/framing.py backend/tests/unit/test_framing.py
git commit -m "feat(kit): ask Claude when a channel's framing looks off and swap the webcam box"
```

---

### Tarefa 4: Ligar ao render dos clipes de live e salvar o frame de referência

**Arquivos:**
- Modificar: `backend/src/live_scoring.py` (`gate_live_segments`)
- Modificar: `backend/src/services/video_service.py` (~linha 1074, kwargs de `create_optimized_clip`)
- Modificar: `backend/src/video_utils.py` (assinatura de `create_optimized_clip`, ~linha 282; ramo
  `streamer_split`, ~linha 421)
- Modificar: `backend/src/media/streamer_split.py` (`plan_streamer_split`)
- Modificar: `backend/src/media/framing.py` (`resolve_framing`, `save_reference_frame`)
- Modificar: `CLAUDE.md` (uma linha na seção do Clip studio/kits: "Enquadramento por canal" e
  `media/framing.py`)
- Teste: `backend/tests/unit/test_live_scoring.py`, `backend/tests/unit/test_framing.py`

**Interfaces:**
- Consome: `decide`, `ask_claude`, `quick_check`, `box_to_rect`, `rect_to_box` (Tarefas 2–3);
  `find_streamer_cam`, `find_cam_rect`, `_sample_frames` (`streamer_split.py`).
- Produz: `segment["edit_kit"]["framing"] = {"channel_id": str, "recording_id": str}` (só quando o kit tem
  `layout`).
- Produz: kwarg `kit_framing: Optional[dict] = None` em `create_optimized_clip`, repassado a
  `plan_streamer_split(input_path, width, height, title, framing=None)`.
- Produz: `resolve_framing(input_path: Path, width: int, height: int, ids: dict, samples, frames) ->
  Optional[FramingDecision]`. Relê o canal e a live com `sync_db`; devolve `None` se o canal não tiver
  caixas.
- Produz: `save_reference_frame(input_path: Path, channel_id: str) -> None`. Grava um JPG (largura 1280,
  qualidade 80) do meio da fonte se `framing_frame_at` for nulo ou tiver mais de 1 h. A condição fica no
  `UPDATE`, então duas janelas não gravam juntas.

- [ ] **Passo 1: escrever os testes que falham**
  - Em `test_live_scoring.py`, seguindo o estilo do arquivo: com `edit_kit = "catclips_v2"`, cada trecho
    mantido traz `edit_kit["framing"] == {"channel_id": ..., "recording_id": ...}`; com `catclips`, não
    traz `framing`.
  - Em `test_framing.py`:
    - `test_plan_uses_channel_boxes`: com `resolve_framing` (monkeypatch) devolvendo
      `FramingDecision(cam, game, "channel", "")`, o filtro de `plan_streamer_split` contém o `crop` da webcam
      e o do jogo.
    - `test_plan_without_framing_is_unchanged`: com `framing=None`, `resolve_framing` não é chamado.
    - `test_reference_frame_failure_never_breaks_the_plan`: `save_reference_frame` levanta exceção → o plano
      continua.

- [ ] **Passo 2:** `.venv/bin/pytest --no-cov tests/unit/test_live_scoring.py tests/unit/test_framing.py -v`.
  Esperado: FAIL.

- [ ] **Passo 3: implementar**
  - `gate_live_segments` acrescenta `c.id AS channel_id` ao SELECT (`w.recording_id` já está lá).
  - `video_service`: `**({"kit_framing": segment["edit_kit"]["framing"]} if (segment.get("edit_kit") or {}).get("framing") else {})`.
  - `plan_streamer_split`: com `framing`, chama `save_reference_frame` (envolto em `try`/log). Depois chama
    `resolve_framing` com as amostras de rosto que ele já tem e com `_sample_frames`, e usa
    `decision.cam/game` como `cam_rect/game_rect`. Se `decision.cam` for `None`, cai no detector de hoje,
    ainda com `game_rect`. Devolve no 4.º item o rosto achado dentro da webcam, para as legendas por papel.
    Grava `{"source", "reason"}` num dict de relatório opcional que o `video_utils` coloca em
    `plan_report["framing"]` e no log.
  - Qualquer exceção em `resolve_framing` → log e detector de hoje. O render nunca quebra por causa do
    enquadramento.

- [ ] **Passo 4:** `.venv/bin/pytest --no-cov tests/unit/test_live_scoring.py tests/unit/test_framing.py tests/unit/test_streamer_split.py -v`
  e depois `cd backend && .venv/bin/pytest` inteiro. Esperado: tudo PASS.

- [ ] **Passo 5: commit**
```bash
git add backend/src/live_scoring.py backend/src/services/video_service.py backend/src/video_utils.py backend/src/media/streamer_split.py backend/src/media/framing.py backend/tests/unit/test_live_scoring.py backend/tests/unit/test_framing.py CLAUDE.md
git commit -m "feat(live): render live clips with the channel's framing and keep a reference frame"
```

---

### Tarefa 5: Editor de enquadramento na tela do canal

**Arquivos:**
- Criar: `frontend/src/app/live/framing-math.ts`, `frontend/src/app/live/framing-math.test.ts`
- Criar: `frontend/src/app/live/framing-editor.tsx`
- Modificar: `frontend/src/app/live/live-types.ts` (`LiveChannel`, `Settings`)
- Modificar: `frontend/src/app/live/channel-settings.tsx` (abre o editor quando `edit_kit === "catclips_v2"`;
  aviso no topo)
- Modificar: `frontend/messages/{de,en,es,id,pt,zh}/live.json` (chaves `framing.*`)
- Modificar: `frontend/src/lib/feature-tours.ts`, `frontend/messages/*/tours.json` (tour `live-framing`,
  2–3 passos)

**Interfaces:**
- Consome: `PATCH /api/live/channels/{id}` com `{cam_box, game_box}`, `POST /api/live/channels/{id}/framing/undo`,
  `GET /api/live/channels/{id}/framing-frame` (Tarefa 1).
- Produz: `type Box = { x: number; y: number; w: number; h: number }`; em `LiveChannel`: `cam_box`, `game_box:
  Box | null`, `framing_changed_at`, `framing_frame_at: string | null`, `has_framing_previous: boolean`.
- Produz, em `framing-math.ts`: `moveBox(start: Box, dx: number, dy: number): Box`, `resizeBox(start: Box,
  corner: "nw" | "ne" | "sw" | "se", dx: number, dy: number): Box`, `drawBox(ax, ay, bx, by): Box`,
  `CORNER_PRESETS: Record<"nw"|"ne"|"sw"|"se", Box>`, `FULL_FRAME: Box`, `MIN_SIDE = 0.04`. Todas prendem em
  0–1, com o lado oposto ancorado no resize (o mesmo comportamento da referência).

- [ ] **Passo 1: escrever os testes que falham** (`framing-math.test.ts`)

```ts
expect(moveBox({ x: 0.8, y: 0.1, w: 0.3, h: 0.3 }, 0.5, 0)).toEqual({ x: 0.7, y: 0.1, w: 0.3, h: 0.3 });
expect(resizeBox({ x: 0.2, y: 0.2, w: 0.3, h: 0.3 }, "nw", 0.5, 0)).toMatchObject({ x: 0.46, w: 0.04 }); // oposto ancorado em 0.5
expect(resizeBox({ x: 0.5, y: 0.5, w: 0.3, h: 0.3 }, "se", 1, 1)).toEqual({ x: 0.5, y: 0.5, w: 0.5, h: 0.5 });
expect(drawBox(0.6, 0.6, 0.2, 0.1)).toEqual({ x: 0.2, y: 0.1, w: 0.4, h: 0.5 });
expect(drawBox(0.5, 0.5, 0.5, 0.5).w).toBe(MIN_SIDE);
```
(use `toBeCloseTo` campo a campo onde houver soma de ponto flutuante.)

- [ ] **Passo 2:** `cd frontend && pnpm exec vitest run src/app/live/framing-math.test.ts`. Esperado: FAIL.

- [ ] **Passo 3: implementar**
  - `framing-math.ts` com as funções acima. Presets de canto = os da referência
    (`nw: {x:0.012,y:0.02,w:0.28,h:0.36}`, `ne: x 0.708`, `sw/se: y 0.62`).
  - `framing-editor.tsx`:
    - busca o frame (sem frame → texto `framing.noFrame`);
    - `<img>` com duas caixas absolutas (vermelha "Webcam", azul "Gameplay") e alças nos cantos, com
      `pointerdown/move/up` e `setPointerCapture`;
    - um seletor de qual caixa desenhar ao arrastar na imagem vazia, os botões dos 4 cantos e "Jogo = tela toda";
    - prévia 9:16 em CSS: topo 720/1920 com o frame como `background` recortado pela `cam_box`, barra amarela
      `#FEC803` 200/1920, e o jogo encaixado em 1000/1920 com `object-fit: contain` sobre a cópia borrada;
    - "Salvar" (PATCH), "Limpar" (`null`, volta ao automático) e "Cancelar";
    - acessível: caixas com `role="group"` e `aria-label`, e setas do teclado movendo 1 % na caixa em foco.
  - Aviso em `channel-settings.tsx` quando `framing_changed_at`: texto `framing.changed` com a data, botão
    "Revisar" (abre o editor) e "Desfazer" (só se `has_framing_previous`).
  - Textos nos 6 idiomas; o pt é a fonte (o dono fala português), os outros traduzidos.
  - Tour `live-framing` (`audience: "existing"`, `releasedAt` = dia do deploy) apontando para
    `data-tour="live-framing"` no botão que abre o editor. O texto explica que marcar a webcam e o jogo uma
    vez deixa todo clipe do canal no mesmo enquadramento, e que o sistema avisa se o layout mudar.
    Seguir `docs/feature-tours.md`.

- [ ] **Passo 4:** `pnpm exec vitest run src/app/live && pnpm run lint && pnpm exec tsc --noEmit && pnpm run test`.
  Esperado: tudo verde (o teste de tours confere o limite de passos).

- [ ] **Passo 5: conferir no app** com dados de demonstração: abrir `/live`, num canal com o kit v2 marcar as
  caixas, salvar, recarregar e ver as caixas mantidas; tirar um screenshot para o PR (regra do `AGENTS.md`).

- [ ] **Passo 6: commit**
```bash
git add frontend/src/app/live/framing-math.ts frontend/src/app/live/framing-math.test.ts frontend/src/app/live/framing-editor.tsx frontend/src/app/live/live-types.ts frontend/src/app/live/channel-settings.tsx frontend/messages/*/live.json frontend/messages/*/tours.json frontend/src/lib/feature-tours.ts
git commit -m "feat(live): framing editor for a channel's webcam and gameplay"
```

> Atenção: `live-types.ts`, `channel-settings.tsx` e `messages/*/live.json` já têm mudanças locais não
> commitadas de outro trabalho. Antes do commit, conferir com `git diff` que só as linhas desta tarefa
> entram (`git add -p` não está disponível; se as mudanças se misturarem, perguntar ao dono antes de commitar).
