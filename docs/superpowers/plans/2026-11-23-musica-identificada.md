# Música identificada: plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (dono escolheu execução inline). Steps use `- [ ]`.

**Goal:** Tirar só a música reconhecida como comercial (ShazamIO + lista de liberados; sem resposta, regra
da música cantada) e pôr música livre da pasta do dono por baixo, onde tirar.

**Architecture:** Tudo em `backend/src/media/music_check.py`. `clean_clip` filtra os trechos com
`decide_spans` antes de separar as vozes e, depois da checagem de sobra, mistura a faixa livre nos trechos
tirados. Shazam isolado em `identify_span` (síncrono, timeout), simulado nos testes.

**Spec:** `docs/superpowers/specs/2026-11-23-musica-identificada-design.md`

## Global Constraints

- Trecho identificado só se tiver ≥ 3 s; consulta com até 12 s do meio do trecho, timeout de 20 s.
- Liberados: `MUSIC_ALLOWLIST` (padrão `/app/uploads/kit-assets/music-allowlist.txt`), um nome por linha,
  comparação sem maiúsculas/minúsculas contra artista, selo e título; sem arquivo: `NoCopyrightSounds`,
  `NCS`, `StreamBeats`.
- Sem resposta do Shazam (exceção ou timeout): tira se a classe AudioSet "Singing" (índice 27) ≥ 0,3 em
  pelo menos 2 s do trecho.
- Música livre: `MUSIC_BED_DIR` (padrão `/app/uploads/kit-assets/music-bed/`), faixa escolhida por
  `sha1(nome do arquivo do clipe)`, ganho 0,12, fade de 0,3 s. Sem faixas: como hoje.
- Status novos: `kept` (havia música, nenhuma tirada). `left`, `removed`, `clean` e `error` como hoje.
- O dicionário de resultado mantém só as chaves que `save_result` grava. O detalhe vai para o log.

## Review Focus

1. Shazam fora do ar ou sem a biblioteca → regra da música cantada; nunca `error` por isso. Teste `test_shazam_down_falls_back_to_singing`.
2. Todos os trechos mantidos → status `kept`, arquivo intocado. Teste `test_all_spans_kept_leaves_the_clip_alone`.
3. Lista com linha em branco ou comentário → ignorada. Teste `test_allowlist_ignores_blanks_and_comments`.
4. Pasta de música livre vazia → só tira a música. Teste `test_no_bed_tracks_just_removes`.
5. Faixa livre menor que o trecho → repete até cobrir. Teste `test_bed_loops_to_cover_the_span`.

---

### Task 1: Decidir trechos e misturar a música livre

**Files:** Modify `backend/src/media/music_check.py`, `backend/pyproject.toml`, `backend/uv.lock`; Test `backend/tests/unit/test_music_check.py`.

**Interfaces:**
- `allowed_names(path: Path | None = None) -> list[str]`
- `is_allowed(track: dict, names: list[str]) -> bool` (`track` = `{"title", "artist", "label"}`)
- `identify_span(path: Path, start: float, end: float) -> dict | None` (None = Shazam não reconheceu; exceção = falhou)
- `decide_spans(spans, identify, singing: np.ndarray, names) -> tuple[list[span], list[dict]]` (trechos a tirar, relatório)
- `bed_track(clip: Path, folder: Path) -> Path | None`; `add_bed(audio: np.ndarray, bed: np.ndarray, spans, rate) -> np.ndarray`

- [ ] Testes FAIL → PASS: os 5 do Review Focus + `test_recognized_commercial_is_removed`, `test_allowed_artist_is_kept`,
  `test_unrecognized_is_kept`, `test_short_spans_follow_todays_rule`, `test_bed_track_is_stable_per_clip`.
- [ ] `shazamio` em `pyproject.toml`; `uv lock`; rebuild da imagem do backend.
- [ ] Commit `feat(music): remove only recognized commercial music and lay a free track under it`.

### Task 2: Teste real e publicação

- [ ] Criar `kit-assets/music-bed/` e `music-allowlist.txt` (padrões) no disco de dados.
- [ ] Reiniciar backend e workers (0 processando); rodar `clean_clip` numa cópia de clipe de GTA com rádio e
  num com trilha do jogo; conferir o log e ouvir o espectro (música removida só no primeiro).
- [ ] Revisão final, segredos no diff, push.
