# Enquadramento por canal (webcam × gameplay) com verificação automática

Data: 2026-11-21 · Status: parte manual implementada em 2026-11-22 (o dono pediu "faz manualmente").

**O que entrou (2026-11-22):** caixas por canal (`cam_box`, `game_box`) salvas na tela do canal e usadas no
render; "Sem webcam" = só a caixa do jogo (o topo mostra o jogo desfocado); frame de referência numa coluna
`bytea` (`framing_frame`), atualizado pelo render a cada 30 min; migração `20261122_0021`.
**Checagem automática (2026-11-22, migração `20261122_0022`):** `media/framing.py` (`decide` + `resolve`). Diferenças
em relação ao desenho abaixo, decididas na construção:
- checagem local só pelo rosto dentro da caixa da webcam (sem a razão de movimento); canal "sem webcam" falha
  quando aparece um rosto parado;
- o Claude roda pelo Claude Code CLI da assinatura (`run_cli_images`, como o filtro de gore), modelo `opus`
  (`FRAMING_CHECK_MODEL`), e responde também `normal_scene` ("começando já"/intervalo: não decide e, no
  início da live, tenta de novo no próximo clipe) e `webcam_box` (onde ele viu a webcam);
- quando a proposta do detector é ruim, a caixa que o Claude viu é desenhada e conferida numa segunda olhada
  antes de trocar;
- canais sem caixas também são conferidos no início de cada live e ficam marcados sozinhos (webcam + jogo
  inteiro, ou "sem webcam");
- custo limitado: depois de um erro ou de uma tela "começando já", a checagem fica pendente e volta em 10 min
  (não a cada clipe), com no máximo 12 consultas por live; cada consulta espera no máximo 60 s;
- no lugar de `framing_auto_off` por live, uma chave por canal, `framing_auto` ("Ajustar sozinho quando o layout
  mudar"): desligada, o sistema só avisa. "Desfazer" volta as caixas de antes da primeira troca automática e
  desliga a chave; "Desfazer" e "Está certo" reusam o `PATCH` (sem rota `/framing/undo`); salvar caixas limpa o
  aviso (`framing_notice`: `changed` ou `attention`).

## Intenção

No layout Cat Clips v2 (`streamer_split`), a webcam do streamer vai em cima e o gameplay embaixo. Hoje a
webcam é achada sozinha a cada clipe (rosto parado + bordas do retângulo). Quando o detector erra, a câmera
ou o gameplay saem deslocados, e o clipe é postado assim.

O dono gostou do seletor do projeto `cortador-automatico`: marcar num frame da live duas caixas, a
**webcam** e o **gameplay**, e todo clipe sair recortado exatamente ali, padronizado e centralizado. Ele
quer isso **por canal** (o layout de um streamer quase nunca muda) e quer que o sistema **perceba sozinho
quando o streamer mudar a webcam de lugar**, em vez de seguir postando clipes tortos.

## Metas

- Na tela do canal (`/live`), uma seção **Enquadramento**: um frame recente da live com duas caixas
  arrastáveis (webcam e gameplay), atalhos dos 4 cantos para a webcam, e uma prévia de como o clipe fica.
- As caixas salvas mandam no render dos clipes daquele canal: a webcam e o gameplay saem exatamente dessas
  regiões. Sem caixas salvas, nada muda (detector de hoje).
- **Checagem rápida em todo clipe** (local, < 1 s, sem custo) antes do render: as caixas ainda batem?
- **Claude confere um frame** na primeira janela de cada live nova e sempre que a checagem rápida falhar.
- **Layout mudou e o Claude confirmou a caixa nova → aplica sozinho e continua postando** (decisão do dono,
  opção "a"). A caixa antiga fica guardada, e o canal mostra um aviso com o frame novo, "Revisar" e "Desfazer".

## Fora do escopo

- Seletor por clipe ou para vídeos avulsos (YouTube/upload), e ajuste no estúdio.
- Proporção do topo e linha divisória ajustáveis (o v2 tem a barra amarela fixa).
- Outros kits além do Cat Clips v2 (só ele tem o `streamer_split`).
- O Claude escolher momentos com base no layout: a seleção de trechos é pelo transcript. As caixas ajudam o
  render e as legendas por papel (o rosto da webcam alimenta `label_words`), não a escolha dos trechos.

## Anti-metas

- Pausar a postagem por causa da verificação (o dono escolheu seguir postando).
- Uma chamada ao Claude por clipe: o custo tem de ficar em torno de uma imagem por live.
- Trocar as caixas do canal com base só na checagem local, sem confirmação do Claude.

## Abordagens consideradas

- **A — Caixas fixas por canal que mandam no render (escolhida).** Determinístico, igual ao projeto de
  referência; o detector vira reserva.
- B — A caixa só como dica para o detector: o erro de deslocamento pode voltar. Descartada.
- C — Marcar a cada clipe, no estúdio: trabalho repetido para quem clipa sempre o mesmo canal. Fica para depois.

Para a verificação:
- **Verificar a fonte antes do render (escolhida)**, com checagem local em todo clipe e Claude só quando há
  dúvida. Evita refazer um render inteiro.
- Verificar o vídeo final com o Claude (a ideia inicial do dono): custo por clipe e um render perdido a
  cada erro.

## Desenho

### Dados (migração `backend/src/migrations/sql/20261121_00xx_live_channel_framing.sql` + `init.sql`)

`live_channels`:
- `cam_box JSONB NULL`, `game_box JSONB NULL`: `{"x","y","w","h"}` em frações 0–1 do frame da fonte.
- `framing_previous JSONB NULL`: `{"cam_box","game_box"}` antes da última troca automática (para "Desfazer").
- `framing_changed_at TIMESTAMPTZ NULL`: quando o sistema trocou as caixas sozinho (o aviso some ao salvar
  ou desfazer).
- `framing_frame_key TEXT NULL`, `framing_frame_at TIMESTAMPTZ NULL`: o frame de referência no bucket.

`live_recordings`:
- `framing_checked_at TIMESTAMPTZ NULL`: a última vez que o Claude conferiu o enquadramento desta live.
- `framing_auto_off BOOLEAN NOT NULL DEFAULT false`: o dono desfez uma troca nesta live; até ela acabar, o
  sistema não troca mais as caixas sozinho (só avisa).

### Frame de referência

O worker já tem a fonte de cada janela da live. No render de uma janela de canal com kit v2, ele salva um
JPG de um instante do meio da janela em `media/live/channels/<channel_id>/framing.jpg` (sobrescreve) quando
o frame salvo não existe ou tem mais de 1 h. Segue a regra do projeto: estado compartilhado vai para o
bucket, nunca para `TEMP_DIR`. `GET /live/channels/{id}/framing-frame` serve o JPG ao dono do canal.

Antes da primeira live gravada não há frame, e a seção mostra "aparece quando o canal entrar ao vivo".

### Caminho até o render

1. `live_scoring.gate_live_segments` já lê `c.edit_kit` e grava `segment["edit_kit"]`. Passa a gravar também
   `framing = {"channel_id", "recording_id"}` dentro de `segment["edit_kit"]`.
2. `video_service`, logo antes de cada render, **relê** do banco `cam_box`, `game_box`, `framing_checked_at`
   e `framing_auto_off`. Relê porque outra janela renderizando ao mesmo tempo pode ter trocado as caixas
   depois que os trechos foram escolhidos. Repassa tudo para `create_optimized_clip` (novo kwarg
   `kit_framing`), e daí para `plan_streamer_split`. O plano roda num thread; as gravações no banco a partir
   dele usam `sync_db.py`, como no ledger de replicação.
3. `plan_streamer_split(input, w, h, title, framing=None)`:
   - sem `framing` ou sem caixas: igual a hoje;
   - com caixas: roda a checagem rápida; passou → `build_split_filter` com `cam_rect` = caixa da webcam e
     região do jogo = caixa do gameplay (em pixels, pares); falhou ou `needs_check` → verificação pelo Claude.
4. `build_split_filter` ganha `game_rect` opcional. Sem ele, usa `game_crop` como hoje. O jogo segue
   "inteiro, sem cortar, centralizado sobre a cópia borrada" (regra do dono); só a região de origem muda.

### Checagem rápida (local)

Com os frames que o plano já amostra (`analyze_vertical_clip` e `_sample_frames`), duas condições:
- **rosto na webcam:** em pelo menos metade das amostras há um rosto com centro dentro da caixa da webcam;
- **webcam estável:** a mudança média entre frames dentro da caixa da webcam é bem menor que dentro da
  caixa do gameplay (razão abaixo de um limiar). É o sinal do `detectarWebcam` da referência. Cena parada
  (o gameplay quase não muda) conta como "passou", porque não dá para decidir.

Os limiares são calibrados com frames sintéticos nos testes; números iniciais ficam a cargo do construtor.

### Verificação pelo Claude

Função síncrona em `backend/src/media/framing_check.py` (roda no thread do render, cliente `anthropic`
como em `ai_edit_service`, modelo `claude-sonnet-5-5` sobreposto por `FRAMING_CHECK_MODEL`, timeout ~20 s):

- **Quando chamar:** (a) a live ainda não foi conferida (`framing_checked_at IS NULL`), ou (b) a checagem
  rápida falhou e a última conferência tem mais de 30 min. Para chamar, a janela reserva a vez com
  `UPDATE live_recordings SET framing_checked_at = now() WHERE id = :id AND (framing_checked_at IS NULL OR
  (:failed AND framing_checked_at < now() - interval '30 minutes')) RETURNING id`. Assim janelas
  renderizando ao mesmo tempo não chamam duas vezes, e um canal que falha sempre (câmera sem borda, cena
  escura, streamer fora da câmera) custa no máximo ~2 imagens por hora. Se a chamada der erro, a reserva é
  desfeita (`framing_checked_at` volta ao valor anterior) para a próxima janela tentar de novo.
- **Entrada:** 1 frame da janela (JPG ~1280 px) com a caixa da webcam desenhada em vermelho e a do jogo em
  azul. Sempre que o Claude é chamado, também se calcula a proposta do detector (`find_streamer_cam` +
  `find_cam_rect`). Se ela existir, vai um segundo frame com a caixa proposta da webcam e o **mesmo
  `game_box` do canal**: quando a webcam muda de lugar, o gameplay continua sendo a tela do jogo, e a faixa
  vertical do `game_crop` nunca vira caixa de jogo, para não cortar o jogo.
- **Saída estruturada** (sem campos opcionais): `current_ok: bool`, `proposal_ok: bool`, `reason: str`
  (`proposal_ok` é false quando não há proposta).

Resultado. Uma troca exige que o Claude reprove a caixa atual **e** aprove a proposta do detector, então
nunca acontece só com a checagem local:
| Situação | Claude | O clipe usa | O canal |
|---|---|---|---|
| checagem passou e não há chamada | — | caixas do canal | nada |
| checagem falhou, mas sem chamada (conferida há < 30 min) | — | caixas do canal (o Claude as aprovou há pouco; a checagem local sozinha não decide) | nada |
| qualquer chamada | atual ok | caixas do canal | nada |
| qualquer chamada, `framing_auto_off` falso | atual errada, proposta ok | proposta | troca a webcam (jogo fica), guarda as antigas, `framing_changed_at = now()` |
| qualquer chamada, `framing_auto_off` verdadeiro | atual errada, proposta ok | proposta | não troca; `framing_changed_at = now()` (aviso) |
| qualquer chamada | atual errada, sem proposta boa | detector de hoje (ou topo borrado) | `framing_changed_at = now()` sem trocar: aviso "o enquadramento precisa de atenção" |
| qualquer chamada | erro, sem chave ou timeout | caixas do canal | nada (log); reserva desfeita |

O resultado vai para `plan_report["framing"]` (`source: channel|proposal|detector`, `reason`) e para os
logs, para auditar no Axiom.

### API (`backend/src/api/routes/live.py`)

- `PATCH /live/channels/{id}` aceita `cam_box` e `game_box`: valida 0 ≤ x,y; w,h ≥ 0,04; x+w ≤ 1; y+h ≤ 1.
  Ambos ou nenhum; `null` limpa os dois. Salvar limpa `framing_changed_at`.
- `POST /live/channels/{id}/framing/undo`: restaura `framing_previous`, limpa o aviso e marca
  `framing_auto_off = true` na live em andamento do canal (se houver), para o sistema não trocar de novo a
  mesma caixa 30 min depois. Salvar as caixas à mão também desliga a troca automática na live em andamento.
- `GET /live/channels/{id}/framing-frame`: o JPG (404 se ainda não há).
- A listagem de canais devolve `cam_box`, `game_box`, `framing_changed_at`, `framing_frame_at` e se há
  `framing_previous`.

### Tela (`frontend/src/app/live/`)

- Novo `framing-editor.tsx`, aberto a partir de `channel-settings.tsx` e visível só quando o kit do canal é
  o Cat Clips v2. O frame vem como `<img>`; duas caixas com `pointer` events (mover, redimensionar pelos
  cantos, desenhar direto na imagem): o mesmo comportamento da referência, reescrito em React.
- Atalhos: 4 cantos para a webcam e "jogo = tela toda".
- Prévia 9:16 em CSS: o frame como fundo recortado pela caixa da webcam (topo 720/1920), a barra amarela
  (200) e o recorte do gameplay encaixado (1000). É aproximada, sem render no servidor.
- Aviso no cartão do canal quando `framing_changed_at` está preenchido: "O enquadramento mudou em <data> e
  foi ajustado sozinho", com "Revisar" e "Desfazer".
- Textos em `frontend/messages/*/live.json` (de, en, es, id, pt, zh).
- Tour (`feature-tours.ts` + `data-tour` + `tours.json`) explicando para que serve marcar o enquadramento.

### Testes

- `build_split_filter` com `cam_rect` e `game_rect` das caixas (frações → pixels pares, limites).
- Checagem rápida: frames sintéticos (caixa parada com "rosto" vs gameplay mudando; caixa no lugar errado).
- Tabela de decisão da verificação com o Claude mockado, incluindo a reserva por live, o intervalo de 30 min,
  a reserva desfeita em erro e `framing_auto_off`.
- Validação do `PATCH` e do undo.
- Frontend: a matemática de arrastar/redimensionar presa em 0–1 (vitest).

## Decisões minhas (para o dono conferir)

- Quando a checagem local falha mas o Claude conferiu há menos de 30 min, o clipe usa as caixas do canal, e
  não o detector: o detector é justamente o que erra hoje.
- No máximo ~2 chamadas ao Claude por hora por live, mesmo num canal que falha sempre.
- A troca automática muda só a caixa da webcam; a do gameplay fica a que o dono marcou.
- "Desfazer" (ou salvar à mão) desliga a troca automática até o fim daquela live; os avisos continuam.
- A troca vale também quando a checagem local passou mas o Claude reprovou a caixa atual na primeira
  janela da live, desde que ele aprove a proposta.

## O que fica a cargo do construtor

- Limiares da checagem rápida e quantos frames usar.
- Texto exato do prompt ao Claude e o formato do desenho das caixas.
- Instante do frame de referência e a resolução do JPG.
- Layout visual exato da seção, seguindo os componentes de `/live`.

## Riscos

- O Claude errar e aprovar uma proposta ruim: os clipes saem tortos até o dono ver o aviso. Mitigação:
  "Desfazer" com um clique e o motivo logado.
- O frame de referência fica até 1 h atrás da live, então para marcar logo no começo da live pode ser
  preciso esperar a primeira janela.
