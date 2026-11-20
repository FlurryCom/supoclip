# Reformular: pegar cortes prontos de um canal e postar com a identidade Cat Clips

Data: 2026-11-20 · Status: aguardando aprovação do dono

## Intenção

O dono quer aumentar o volume de cortes postados nas contas Cat Clips (TikTok, YouTube "Dossiê", Instagram)
aproveitando cortes que outros canais já fizeram. Ele cola o link de um canal, vê os vídeos que o canal postou,
marca os que quer, e o SupoClip refaz cada um com o kit Cat Clips (legenda Archivo Black, sininho + whoosh no
começo, boom no pico, CTA de cada rede) e posta nas contas escolhidas — na hora que ele mandar ou sozinho assim
que terminar.

## Metas

- Tela nova **"Reformular"** no menu, ao lado de "Ao vivo" (escolha do dono, opção A).
- Colar link de canal → lista dos vídeos recentes, com capa, título e duração → marcar vários.
- Cada vídeo marcado vira **uma geração na Biblioteca**, com o vídeo inteiro (não corta, não procura momentos).
- O kit é aplicado como nas lives; o borrão de nudez/gore também.
- Publicar: escolher as contas na própria tela, com a opção **"Publicar automaticamente ao terminar"**.
  Sem essa opção, o vídeo pronto fica na Biblioteca e sai pelo botão **Publicar** que já existe.
- A postagem segue as mesmas regras da postagem automática: CTA, borrão (fail closed, >40% borrado não posta),
  intervalo da postagem segura por conta, limite diário se ligado, nunca postar duas vezes o mesmo clipe numa conta,
  só contas autorizadas.

## Fora do escopo (por enquanto)

- Vigiar o canal e reformular sozinho os vídeos novos (dá para pôr depois em cima desta tela).
- Vídeos longos que precisam ser cortados (já existe a tela Criar para isso).
- Instagram como **fonte**: o yt-dlp não lista perfis do Instagram sem login (testado: "Unsupported URL").
  A tela mostra um aviso. Postar **no** Instagram continua normal.

## Anti-metas

- Postar em conta que não foi autorizada.
- Reformular o mesmo vídeo duas vezes sem querer.
- Travar as lives: a fila de reformulação não pode tomar o lugar das janelas de live.

## O que já existe e será reaproveitado

- **Listagem**: o yt-dlp (`/app/.venv/bin/yt-dlp` 2026.10.21 no backend) lista, testado:
  - YouTube: `https://www.youtube.com/@canal/shorts` (duração vem vazia na listagem rápida);
  - Twitch: `https://www.twitch.tv/<canal>/clips?filter=clips&range=7d` (com duração);
  - TikTok: `https://www.tiktok.com/@perfil` (com duração).
- **Baixar**: YouTube (`youtube_utils`) e clipe da Twitch (`stream_source`, tipo `clip`) já são aceitos como fonte de
  tarefa. TikTok **não** é aceito hoje (`determine_source_type` recusa) — precisa ser adicionado.
- **Kit**: `media/edit_kit_render.apply_edit_kit`, chamado em `video_service.create_single_clip` quando o segmento tem
  `edit_kit`. Hoje só o portão das lives (`live_scoring.gate_live_segments`) põe o kit no segmento.
- **Borrão**: `segment["sensitive_check"] = True` faz a análise de nudez/gore do clipe renderizado.
- **Publicar**: tabela `live_post_requests` + rota `POST /live/publish` + `post_clips` do gravador. Hoje só aceita
  clipes de live (`live_clips`), porque a busca do gravador junta canal e gravação da live.
- **Legenda do post**: `postiz.caption(hook_title, channel, live_title, platform)` já credita a fonte ("🎮 canal").

## Desenho

### Tela "Reformular" (`/reformular`)

```
[ colar link do canal ............................ ] [Buscar]
YouTube Shorts · TikTok · clipes da Twitch   (Instagram ainda não)

☑ [capa] Título do vídeo 1 · 0:32 [menos de 1 min]
☐ [capa] Título do vídeo 2 · 0:58 [menos de 1 min]   (já reformulado ✓, desabilitado)
☑ [capa] Título do vídeo 3 · 1:10
☐ [capa] Vídeo longo · 12:40               (desabilitado: longo demais)
...                                         [carregar mais]

Kit: [Cat Clips ▾]
Contas: ☑ TikTok Cat Clips  ☑ YouTube Dossiê  ☑ Instagram Cat Clips
[ ] Publicar automaticamente ao terminar
                                   [Reformular 2 vídeos]

Em andamento / prontos (desta tela): título · status · link para a geração
```

- Mesmo visual das telas Ao vivo / Biblioteca (componentes existentes, botão flutuante não precisa: a ação é o botão).
- Mostra os 30 vídeos mais recentes; "carregar mais" busca os próximos 30.
- Só aceita vídeos de até **3 minutos** (corte pronto); os mais longos aparecem desabilitados.
- Vídeos já reformulados por este usuário aparecem marcados e desabilitados.
- **Vídeos com menos de 1 minuto (decisão do dono)**: logo depois de buscar, ganham uma tag "menos de 1 min" na lista,
  para avisar que o TikTok não monetiza. Continuam podendo ser escolhidos e postados normalmente. Como a listagem
  rápida do YouTube vem sem duração, a duração precisa ser buscada para mostrar a tag.
- Escondida para quem não tem a flag de live (mesma regra do item "Ao vivo" no menu); contas e publicar só para
  usuários liberados no Postiz (mesma regra de `_postiz_user`).

### Backend

- `GET /reformular/list?url=&page=` → valida o link (só hosts suportados), roda o yt-dlp em modo lista rápida
  (`--flat-playlist`, sem baixar), devolve `{platform, items:[{id, url, title, duration, thumbnail, done}]}`.
  O link é normalizado para a aba certa (YouTube → `/shorts`, Twitch canal → `/clips`).
- `POST /reformular` `{items:[{platform,id,url,title}], edit_kit, target_ids, auto_publish}` → para cada vídeo novo
  cria uma tarefa normal (aparece na Biblioteca) e uma linha em `reform_items`.
- Tabela nova `reform_items (id, user_id, platform, video_id, source_url, source_channel, task_id, edit_kit,
  auto_publish, target_ids, created_at, UNIQUE(user_id, platform, video_id))`.
- **Processamento**: um modo de tarefa "inteiro" — um único segmento do começo ao fim do vídeo, sem a análise de
  momentos da IA, com `edit_kit` (sem chat) e `sensitive_check`. O "boom" vai no momento mais alto do áudio
  (sem chat, não há pico de chat). Título do gancho (`hook_title`) gerado como nas outras tarefas.
- **Legenda que já vem no vídeo (decisão do dono, opção b)**: a nossa legenda Archivo Black entra numa faixa que
  cobre a parte de baixo do vídeo, onde a legenda do outro canal costuma ficar, escondendo a deles. Antes de fechar o
  tamanho/posição da faixa, mostrar uma prévia curta ao dono (vídeo 9:16 e clipe 16:9 da Twitch). O Whisper continua
  rodando para gerar a nossa legenda e o título do gancho.
- **TikTok como fonte**: aceitar link de vídeo do TikTok no download (yt-dlp).
- **Publicar**:
  - O botão Publicar da Biblioteca e a rota `/live/publish` passam a aceitar clipes reformulados também.
  - Com "publicar automaticamente", ao terminar a tarefa entram pedidos em `live_post_requests` para as contas salvas.
  - O gravador (`post_clips`) passa a buscar também os pedidos de clipes reformulados (que não têm gravação de live),
    com as mesmas regras. A legenda credita o canal de origem ("🎮 @perfil").
- **Fila (decisão minha)**: as tarefas da conta rodam uma por vez, na ordem de chegada
  (`VIDEO_JOB_USER_CONCURRENCY=1`, `workers/video_admission.py`), e as janelas de live usam a mesma fila. Para não
  travar as lives, `POST /reformular` só grava os pedidos (`reform_items` com status `pending`); um laço solta **um
  vídeo por vez**, e só quando não há janela de live esperando na fila. 30 vídeos marcados não passam na frente das lives.
- **Repetir (decisão minha)**: um vídeo conta como "já reformulado" enquanto a geração dele existe e não falhou. Se a
  tarefa falhar ou o dono apagar a geração na Biblioteca, o vídeo volta a ficar disponível (a linha em `reform_items`
  é reaproveitada, não duplicada).

## Abordagens consideradas

- **A — Tela nova "Reformular"** (escolhida pelo dono): tudo do fluxo num lugar só; resultado cai na Biblioteca.
- B — Seção dentro de "Ao vivo": a tela já é longa e é sobre monitorar lives; misturaria duas coisas.
- C — Na tela "Criar": aproveita a entrada, mas Criar é para cortar vídeo longo; contas e publicar sozinho ficam
  espremidos.

## Restrições

- Docker/Podman rootless; migrations SQL em `backend/src/migrations/sql` (próximo número livre: 0018).
- Gravador (`live/`) é empacotado na imagem: rebuild a cada mudança. Mensagens do frontend também (rebuild).
- Tudo que o dono lê em português; textos da tela nos 6 idiomas como o resto.
- Nunca gravar chaves/segredos em arquivo; nunca postar em conta não autorizada.

## Riscos

- **Conteúdo reaproveitado**: YouTube e TikTok podem tratar repost de vídeo alheio com uma camada por cima como
  "conteúdo reaproveitado / não original" (menos alcance, sem monetização, ou strike de direitos autorais).
- TikTok muda o site com frequência; a listagem/baixa pelo yt-dlp pode quebrar até atualizar o yt-dlp.

## Deixado para quem constrói

- Como detectar o momento mais alto do áudio (ffmpeg/astats ou similar).
- Como o modo "inteiro" entra no pipeline (novo `workflow` ou atalho antes da análise), desde que não mude as lives.
- Onde disparar os pedidos automáticos (fim da tarefa no worker ou no laço do gravador).
- Layout para vídeo de origem já vertical (9:16) versus horizontal (clipe da Twitch 16:9): seguir o layout das lives
  para 16:9; para 9:16, mostrar uma prévia ao dono antes de fechar onde ficam a legenda e o CTA.
