# Tirar música dos clipes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (owner chose inline execution). Steps use `- [ ]`.

**Goal:** Nos clipes dos kits Cat Clips, detectar música, trocar o áudio dos trechos com música só pelas vozes, e
não postar clipes em que a música continua.

**Architecture:** Módulo novo `backend/src/media/music_check.py` (detector PANNs Cnn10 em torch + separador MDX-Net
Kim_Vocal_2 em onnxruntime). `video_service` chama `clean_clip` depois de `create_optimized_clip` e antes de
`apply_edit_kit`, e grava o resultado em `live_clip_music`. O recorder recusa clipes com `status = 'left'`.

**Tech Stack:** Python, numpy, torch (já instalado), onnxruntime (já instalado), ffmpeg, Postgres.

**Spec:** `docs/superpowers/specs/2026-11-22-musica-copyright-design.md`

## Global Constraints

- Modelos em `/app/uploads/models/music/`, baixados uma vez, conferidos por sha256 (arquivo ruim é apagado):
  - `cnn10.pth` — `https://zenodo.org/api/records/3987831/files/Cnn10_mAP=0.380.pth/content`, sha256
    `5240bdc47444e331bbeb54fd741d2b3f933a4526e84b0c17bf3593df94b13962`; `torch.load(..., weights_only=True)`.
  - `Kim_Vocal_2.onnx` — `https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx`,
    sha256 `ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b`.
- Detector: 32 kHz mono, janela de 2 s centrada em cada segundo, classe AudioSet 137 ("Music").
- Separador: 44,1 kHz estéreo, n_fft 7680, hop 1024, dim_f 3072, dim_t 256; sessão com `enable_cpu_mem_arena=False`,
  `enable_mem_pattern=False`, 6 threads. Uma separação por vez por processo (lock), pico medido 1,9 GB.
- Status: `clean` | `removed` | `left` | `error`. Falha → `error`, arquivo intacto, clipe segue como hoje.
- Motivo da recusa (o dono lê): `"música com direitos autorais"`.
- Kit: `"music_clean"` False em `none`; False em `catclips`/`catclips_v2` até a prévia aprovada (Task 4).
- Vídeo nunca reencodado (`-c:v copy`); duração do arquivo não muda.

## Review Focus

1. Clipe sem áudio → `clean`, nada quebra. Teste `test_clip_without_audio_is_clean`.
2. Falha no meio da troca do áudio → arquivo original intacto. Teste `test_a_failure_leaves_the_clip_untouched`.
3. Trechos de música nas bordas (começo/fim do clipe) e transição suave. Teste `test_mix_spans_swaps_only_music_stretches`.
4. Clipe pedido à mão na Biblioteca com música restante → recusado também. Teste `test_music_left_refuses_every_post`.
5. Clipe feito antes da feature (sem linha em `live_clip_music`) → posta como hoje. Mesmo teste.

---

### Task 1: Detector, separador e limpeza do clipe

**Files:** Create `backend/src/media/music_check.py`; Test `backend/tests/unit/test_music_check.py`

**Interfaces — Produces:**
- `ensure_model(name: str) -> Path | None` (name em `MODELS`, dict nome → (url, sha256)).
- `music_per_second(mono32: np.ndarray) -> np.ndarray` (chance de música por segundo).
- `music_spans(probs: np.ndarray, threshold: float = 0.3, max_gap: int = 2, min_len: int = 2) -> list[tuple[float, float]]`
  (segundos; buracos ≤ `max_gap` juntam; trechos < `min_len` saem).
- `separate_vocals(stereo44: np.ndarray) -> np.ndarray` (mesma forma (2, L)).
- `mix_spans(original: np.ndarray, vocals: np.ndarray, spans, rate: int, fade: float = 0.3) -> np.ndarray`.
- `clean_clip(path: Path) -> dict` → `{"status", "music_seconds", "error"}`; troca o áudio no lugar (arquivo
  temporário + `os.replace`), nunca levanta.

- [ ] Testes: `test_music_spans_joins_short_gaps_and_drops_blips` (`[.1,.8,.8,.1,.8,.8,.8,.1,.1,.1,.9,.1]` →
  `[(1.0, 7.0)]`); `test_mix_spans_swaps_only_music_stretches` (fora: igual ao original; meio do trecho: igual às
  vozes; trecho no começo do sinal funciona); `test_clip_without_audio_is_clean`; `test_no_music_leaves_the_file_as_is`
  (monkeypatch `music_per_second` → zeros; bytes do arquivo iguais); `test_music_is_swapped_for_vocals`
  (fakes: separador devolve silêncio, depois detector dá zero → `removed`, duração igual, vídeo copiado);
  `test_music_that_survives_is_left`; `test_a_failure_leaves_the_clip_untouched` (separador levanta → `error`,
  bytes iguais); `test_a_bad_download_leaves_no_model` (sha errado → None, nada no disco).
- [ ] `scripts/backend-test.sh tests/unit/test_music_check.py` → FAIL; implementar (base: spikes já medidos); → PASS.
- [ ] Commit `feat(audio): find music in clips and keep only the voices`.

### Task 2: Ligar no render e guardar o resultado

**Files:** Create `backend/src/migrations/sql/20261122_0019_live_clip_music.sql`; Modify `backend/src/media/music_check.py`
(`save_result`), `backend/src/edit_kits.py`, `backend/src/services/video_service.py` (logo antes do bloco do edit kit);
Test `backend/tests/unit/test_edit_kits.py`

- Tabela `live_clip_music(file_path varchar primary key, status varchar(8) not null, music_seconds real not null default 0,
  error text, checked_at timestamptz not null default now())`.
- `async save_result(file_path: str, result: dict) -> None` (upsert, como `sensitive_check.save_analysis`).
- `video_service`: se `segment.get("edit_kit")` e `kit.get("music_clean")`: `clean_clip` via `run_in_thread`, salva
  (erro ao salvar só vira aviso no log), loga status e segundos.
- [ ] Teste `test_kits_music_clean_off_until_approved` → FAIL → implementar → PASS (+ `test_edit_kit_render.py`).
- [ ] `docker compose restart backend` (migração) e conferir a tabela. Commit `feat(audio): clean music before the edit kit`.

### Task 3: Recorder recusa clipe com música restante

**Files:** Modify `live/supoclip_live/sensitive.py` (`music_refusal`), `live/supoclip_live/service.py` (`post_clips`
query + checagem logo depois de `refusal_reason`), `live/supoclip_live/reform.py` (`post_candidates` query);
Test `live/tests/test_sensitive.py`

- Queries: `LEFT JOIN live_clip_music m ON m.file_path = g.file_path`, coluna `m.status AS music_status`.
- `music_refusal(row: dict) -> str | None` → `"música com direitos autorais"` só quando `music_status == "left"`.
- [ ] Teste `test_music_left_refuses_every_post` (left → motivo; removed/clean/error/None → None) → FAIL → PASS
  (`/tmp/claude-1000/ltest.sh tests`).
- [ ] Rebuild do recorder e `docker compose --profile live up -d live-recorder`. Commit `feat(live): never post clips that still carry music`.

### Task 4: Prévia real, ligar, revisar e subir

- [ ] No backend, fora do fluxo: `clean_clip` em cópias de 2–3 clipes reais com música (um com rádio cantado).
  Conferir status e tempo; mandar ao dono 15 s antes/depois de cada; esperar aprovação.
- [ ] Aprovado: `"music_clean": True` em `catclips` e `catclips_v2` (teste atualizado); reiniciar o worker com 0 tarefas
  `processing`; revisão final (opus); conferir segredos do `.env` contra o diff; push para `origin` e `brainsidian`
  (`feat/live-clipping` e `main`).
