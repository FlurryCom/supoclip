# Legenda streamer × amigo × jogo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (owner chose inline execution). Steps use `- [ ]`.

**Goal:** No Cat Clips v2, marcar cada palavra da legenda como streamer, amigo ou jogo, com estilos diferentes, e esconder as palavras do jogo quando o próprio jogo já mostra legenda.

**Architecture:** Um módulo novo `backend/src/media/voice_roles.py` recebe o clipe já cortado (`source_clip_path`, mesma linha do tempo das palavras) e as palavras da legenda; devolve as palavras com `role` (e sem as do jogo que já têm legenda na tela). `create_optimized_clip` chama o módulo quando o kit tem `voice_roles` e passa o resultado como `caption_words`; `build_assemblyai_ass_subtitles` quebra blocos na troca de papel e desenha amigo/jogo como linha estática no estilo do papel.

**Tech Stack:** Python, numpy, onnxruntime (CPU, já instalado), OpenCV, ffmpeg, ASS.

**Spec:** `docs/superpowers/specs/2026-11-20-legenda-streamer-jogo-design.md`

## Global Constraints

- Papéis: `"streamer"`, `"friend"`, `"game"`. Falha em qualquer etapa → todas `"streamer"` (como hoje), nunca derruba o clipe.
- Estilos: streamer = Cat Clips atual; friend = mesma fonte e tamanho, cor `#7CCBFF`, contorno `#111111`, sem caixa amarela; game = branco `#FFFFFF`, 75 % do tamanho, contorno `#111111`, sem caixa amarela. Amigo e jogo sem animação de palavra.
- Esconder só palavras `game` cujo trecho tem legenda do jogo visível.
- Modelo de voz: ONNX CAM++ (3D-Speaker, 16 kHz, fbank 80), baixado uma vez para `backend/models/voice/campplus.onnx` (fora do git); sem torchaudio/speechbrain.
- Liga por kit: `"voice_roles": False` no v2 até o dono aprovar a prévia; depois `True`.
- Alvo de tempo: < 10 s a mais por clipe de 1–2 min.
- Tudo que o dono lê em português.

## Review Focus

1. Clipe sem fala nenhuma / uma palavra só → nada quebra, legenda igual a hoje. Teste `test_label_words_without_speech_keeps_everything`.
2. Clipe só com o streamer falando → todas `streamer`. Teste `test_single_voice_is_the_streamer`.
3. Modelo ausente ou falha do onnxruntime → todas `streamer`. Teste `test_model_failure_falls_back_to_streamer`.
4. Sem câmera encontrada → streamer = voz com mais tempo de fala. Teste `test_without_cam_the_longest_voice_is_the_streamer`.
5. Bloco nunca mistura papéis; palavra escondida não deixa buraco no tempo das outras. Teste `test_role_change_breaks_the_caption_block`.

---

### Task 1: Trechos de fala, voz e agrupamento

**Files:** Create `backend/src/media/voice_roles.py`; Test `backend/tests/unit/test_voice_roles.py`

**Interfaces — Produces:**
- `speech_segments(words: list[dict], gap: float = 0.6) -> list[tuple[float, float, list[int]]]` (início, fim, índices das palavras; palavras com `start`/`end` em ms, como no cache de transcrição).
- `fbank(samples: np.ndarray, rate: int = 16000) -> np.ndarray` (frames × 80, log-mel estilo Kaldi: janela 25 ms, passo 10 ms, pré-ênfase 0.97, Povey/Hamming, média subtraída).
- `embed(samples: np.ndarray) -> np.ndarray | None` (vetor normalizado; None se < 0,8 s).
- `cluster(vectors: list[np.ndarray], threshold: float = 0.55) -> list[int]` (aglomerativo por média, cosseno).
- `ensure_model() -> Path | None` (baixa se faltar; None se não der).

- [ ] Testes: `test_speech_segments_split_on_pauses` (palavras 0–1 s, 1,1–2 s, pausa, 3–4 s → 2 trechos); `test_fbank_shape` (1 s de seno → (98..100, 80)); `test_cluster_groups_same_voice` (vetores sintéticos A, A', B → [0, 0, 1]); `test_cluster_single_vector` → [0].
- [ ] Rodar: `scripts/backend-test.sh tests/unit/test_voice_roles.py` → FAIL.
- [ ] Implementar; baixar o modelo uma vez e conferir saída de `embed` em 2 s de áudio real (vetor 192/512, norma 1).
- [ ] Mesmo comando → PASS. Commit `feat(captions): voice segments, embeddings and clustering`.

### Task 2: Boca, legenda do jogo na tela e papéis

**Files:** Modify `backend/src/media/voice_roles.py`; Test `backend/tests/unit/test_voice_roles.py`

**Interfaces — Produces:**
- `mouth_motion(path: Path, face: Box, times: list[float]) -> np.ndarray` (diferença média entre quadros vizinhos na metade de baixo do rosto, em cada tempo).
- `game_subtitles_on(path: Path, times: list[float]) -> list[bool]` (usa `source_captions.caption_lines_in_frame` no quadro inteiro).
- `assign_roles(segments, labels: list[int], talk_motion: dict[int, float], idle_motion: float, subs_share: dict[int, float], turns: dict[int, int]) -> dict[int, str]` (grupo → papel). Regras: streamer = grupo com maior `talk_motion - idle_motion` se a diferença ≥ limiar, senão o grupo com mais tempo de fala; outro grupo com `subs_share ≥ 0.5` → game; com `turns ≥ 2` alternando com o streamer e `subs_share < 0.2` → friend; senão game.

- [ ] Testes: `test_single_voice_is_the_streamer`; `test_mouth_picks_the_streamer` (grupo 1 fala mais, mas a boca mexe no grupo 0 → grupo 0 streamer); `test_without_cam_the_longest_voice_is_the_streamer`; `test_voice_with_game_subtitles_is_game`; `test_voice_talking_back_is_friend`; `test_unsure_voice_is_game`.
- [ ] FAIL → implementar → PASS. Commit `feat(captions): pick streamer, friend and game voices`.

### Task 3: Rotular as palavras e desenhar os estilos

**Files:** Modify `backend/src/media/voice_roles.py` (`label_words`), `backend/src/media/captions.py`, `backend/src/video_utils.py`, `backend/src/edit_kits.py`; Tests `test_voice_roles.py`, `backend/tests/unit/test_captions_roles.py` (novo), `test_edit_kits.py`

**Interfaces:**
- `label_words(clip: Path, words: list[dict], width: int, height: int) -> list[dict]` — cópias das palavras com `role`; palavras `game` em trecho com legenda do jogo saem da lista; qualquer exceção → palavras originais com `role="streamer"`.
- `build_assemblyai_ass_subtitles`: quando `caption_words` têm `role`, quebra de bloco em toda troca de papel (somar aos `passage_breaks`); blocos `friend`/`game` viram um `Dialogue` por linha, estático, com `\c`, `\3c` e (game) `\fscx75\fscy75` e sem caixa/realce.
- `create_optimized_clip`: se `kit_voice_roles` (novo parâmetro) e houver transcrição, calcula as palavras com `get_words_for_keep_ranges`, chama `label_words(source_clip_path, …)` e passa como `caption_words`.
- `edit_kits`: chave `"voice_roles"` (False no none/catclips; False no v2 até a aprovação); `video_service.create_single_clip` repassa `kit["voice_roles"]`.

- [ ] Testes: `test_label_words_without_speech_keeps_everything`; `test_model_failure_falls_back_to_streamer` (monkeypatch `embed` levantando erro); `test_hidden_game_words_are_dropped`; `test_role_change_breaks_the_caption_block` (ASS gerado tem bloco streamer e bloco game separados, o de game com `\fscx75` e `&HFFFFFF&`, o de friend com a cor `#7CCBFF` em BGR `&HFFCB7C&`); `test_v2_kit_has_voice_roles_off_until_approved`.
- [ ] FAIL → implementar → PASS (+ `tests/unit/test_streamer_split.py tests/unit/test_edit_kits.py tests/unit/test_edit_kit_render.py`). Commit `feat(captions): streamer, friend and game caption styles`.

### Task 4: Prévia real, ajuste e ligar

- [ ] Rodar no worker, fora do fluxo, `create_optimized_clip(..., kit_layout="streamer_split", kit_voice_roles=True)` em 2–3 trechos de `/app/uploads/uploads` (um com cutscene/NPC, um com amigo falando, um só com o streamer). Conferir os papéis no log e ajustar `threshold`/limiar da boca se errar.
- [ ] Mandar ao dono 10–15 s de cada; esperar a aprovação.
- [ ] Aprovado: `"voice_roles": True` no v2 (teste atualizado), reiniciar o worker com 0 tarefas `processing`, commit, conferir segredos do `.env` contra o diff, push para `origin` e `brainsidian` (`feat/live-clipping` e `main`).
